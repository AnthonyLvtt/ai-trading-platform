from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atp.observability import (
    AuditJournal,
    EventCategory,
    EventClassification,
    EventModule,
    EventSeverity,
    EventType,
    ObservabilityEvent,
    ObservabilityReasonCode,
    ObservabilityStatus,
    build_event,
    validate_event,
)
from atp.shared.environment import Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import CausationId, ContentIdentity, CorrelationId, EventId

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
SUBJECT = ContentIdentity.from_text("subject")
CORRELATION = CorrelationId(f"correlation:{ContentIdentity.from_text('run')}")


def event(
    *,
    occurred_at: datetime = NOW,
    previous: ContentIdentity | None = None,
    payload: object = None,
    subject_identity: ContentIdentity = SUBJECT,
    causation_id: CausationId | None = None,
) -> ObservabilityEvent:
    result = build_event(
        event_type=EventType.STRATEGY_EVALUATED,
        occurred_at=occurred_at,
        environment=Environment.BACKTEST,
        module=EventModule.STRATEGY,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        correlation_id=CORRELATION,
        causation_id=causation_id,
        subject_type="StrategyEvaluation",
        subject_id="strategy-evaluation:fixture",
        subject_content_identity=subject_identity,
        payload={
            "evaluation_status": "COMPLETED",
            "reason_code": None,
            "signal_kind": "LONG_ENTRY",
            "strategy_id": "sma-crossover",
            "strategy_version": "1.0.0",
        }
        if payload is None
        else payload,
        previous_event_identity=previous,
    )
    assert result.status is ObservabilityStatus.ACCEPTED
    assert result.event is not None
    return result.event


def test_same_event_has_same_identity_and_identifier() -> None:
    payload = {
        "evaluation_status": "COMPLETED",
        "reason_code": None,
        "signal_kind": "LONG_ENTRY",
        "strategy_id": "sma-crossover",
        "strategy_version": "1.0.0",
    }
    first = event(payload=payload)
    second = event(payload=dict(reversed(tuple(payload.items()))))

    assert first == second
    assert first.content_identity == second.content_identity
    assert first.event_id == EventId(f"obs-event:{first.content_identity}")


def test_changed_subject_identity_changes_event_identity() -> None:
    first = event()
    second = event(subject_identity=ContentIdentity.from_text("other-subject"))

    assert first.content_identity != second.content_identity
    assert first.event_id != second.event_id


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "fixture-secret"},
        {"Authorization": "fixture-secret"},
        {"token": "fixture-secret"},
        {"nested": {"private_key": "fixture-secret"}},
    ],
)
def test_sensitive_payload_is_blocked_not_redacted(payload: object) -> None:
    result = build_event(
        event_type=EventType.STRATEGY_EVALUATED,
        occurred_at=NOW,
        environment=Environment.BACKTEST,
        module=EventModule.STRATEGY,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        correlation_id=CORRELATION,
        causation_id=None,
        subject_type="StrategyEvaluation",
        subject_id="strategy-evaluation:fixture",
        subject_content_identity=SUBJECT,
        payload=payload,
        previous_event_identity=None,
    )

    assert result.status is ObservabilityStatus.BLOCKED
    assert result.reason_code is ObservabilityReasonCode.SENSITIVE_DATA_DETECTED
    assert result.event is None
    assert "fixture-secret" not in str(result)


@pytest.mark.parametrize("payload", [object(), {"value": object()}, {1: "bad"}])
def test_malformed_runtime_payload_is_fail_closed(payload: object) -> None:
    result = build_event(
        event_type=EventType.STRATEGY_EVALUATED,
        occurred_at=NOW,
        environment=Environment.BACKTEST,
        module=EventModule.STRATEGY,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        correlation_id=CORRELATION,
        causation_id=None,
        subject_type="StrategyEvaluation",
        subject_id="strategy-evaluation:fixture",
        subject_content_identity=SUBJECT,
        payload=payload,
        previous_event_identity=None,
    )

    assert result.status is ObservabilityStatus.BLOCKED
    assert result.reason_code in {
        ObservabilityReasonCode.INVALID_EVENT_INPUT,
        ObservabilityReasonCode.INVALID_PAYLOAD,
    }


def test_non_utc_event_time_is_blocked() -> None:
    result = build_event(
        event_type=EventType.STRATEGY_EVALUATED,
        occurred_at=datetime(2026, 9, 7),
        environment=Environment.BACKTEST,
        module=EventModule.STRATEGY,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        correlation_id=CORRELATION,
        causation_id=None,
        subject_type="StrategyEvaluation",
        subject_id="strategy-evaluation:fixture",
        subject_content_identity=SUBJECT,
        payload={},
        previous_event_identity=None,
    )

    assert result.status is ObservabilityStatus.BLOCKED
    assert result.reason_code is ObservabilityReasonCode.INVALID_EVENT_TIME


def test_secret_classification_is_not_accepted() -> None:
    result = build_event(
        event_type=EventType.STRATEGY_EVALUATED,
        occurred_at=NOW,
        environment=Environment.BACKTEST,
        module=EventModule.STRATEGY,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        correlation_id=CORRELATION,
        causation_id=None,
        subject_type="StrategyEvaluation",
        subject_id="strategy-evaluation:fixture",
        subject_content_identity=SUBJECT,
        payload={},
        previous_event_identity=None,
        classification=EventClassification.SECRET,
    )

    assert result.status is ObservabilityStatus.BLOCKED


def test_tampered_event_is_detected() -> None:
    observed = event()
    object.__setattr__(
        observed,
        "payload",
        {
            **observed.payload,
            "signal_kind": "EXIT",
        },
    )

    result = validate_event(observed)

    assert result.status is ObservabilityStatus.BLOCKED
    assert result.reason_code is ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE


def test_corrupted_event_identifier_is_detected() -> None:
    observed = event()
    object.__setattr__(observed, "event_id", EventId("obs-event:corrupted"))

    result = validate_event(observed)

    assert result.status is ObservabilityStatus.BLOCKED
    assert result.reason_code is ObservabilityReasonCode.EVENT_IDENTITY_MISMATCH


def test_direct_inconsistent_event_construction_is_rejected() -> None:
    valid = event()
    with pytest.raises(ValidationError):
        ObservabilityEvent(
            EventId("obs-event:invalid"),
            valid.event_type,
            valid.schema_version,
            valid.occurred_at,
            valid.environment,
            valid.module,
            valid.category,
            valid.severity,
            valid.correlation_id,
            valid.causation_id,
            valid.subject_type,
            valid.subject_id,
            valid.subject_content_identity,
            valid.payload,
            valid.previous_event_identity,
            valid.content_identity,
        )


def test_journal_append_chain_is_immutable_and_deterministic() -> None:
    empty = AuditJournal.empty()
    first = event()
    first_append = empty.append(first)
    assert first_append.journal is not None
    second = event(
        occurred_at=NOW,
        previous=first.content_identity,
        subject_identity=ContentIdentity.from_text("second"),
        causation_id=CausationId(str(first.event_id)),
    )
    second_append = first_append.journal.append(second)

    assert second_append.status is ObservabilityStatus.ACCEPTED
    assert second_append.journal is not None
    assert empty.events == ()
    assert second_append.journal.validate().status is ObservabilityStatus.ACCEPTED
    assert (
        second_append.journal.content_identity
        == AuditJournal._from_valid_events((first, second)).content_identity
    )


def test_duplicate_event_is_blocked_without_deduplication() -> None:
    first = event()
    appended = AuditJournal.empty().append(first)
    assert appended.journal is not None

    duplicate = appended.journal.append(first)

    assert duplicate.status is ObservabilityStatus.BLOCKED
    assert duplicate.reason_code is ObservabilityReasonCode.DUPLICATE_EVENT
    assert duplicate.journal is None


def test_non_monotonic_event_time_is_blocked() -> None:
    first = event()
    appended = AuditJournal.empty().append(first)
    assert appended.journal is not None
    earlier = event(
        occurred_at=NOW - timedelta(seconds=1),
        previous=first.content_identity,
        subject_identity=ContentIdentity.from_text("earlier"),
        causation_id=CausationId(str(first.event_id)),
    )

    blocked = appended.journal.append(earlier)

    assert blocked.status is ObservabilityStatus.BLOCKED
    assert blocked.reason_code is ObservabilityReasonCode.NON_MONOTONIC_EVENT_TIME


def test_modified_chain_link_is_detected() -> None:
    first = event()
    first_append = AuditJournal.empty().append(first)
    assert first_append.journal is not None
    second = event(
        previous=first.content_identity,
        subject_identity=ContentIdentity.from_text("second"),
        causation_id=CausationId(str(first.event_id)),
    )
    second_append = first_append.journal.append(second)
    assert second_append.journal is not None
    object.__setattr__(second, "previous_event_identity", ContentIdentity.from_text("tampered"))

    validation = second_append.journal.validate()

    assert validation.status is ObservabilityStatus.BLOCKED
    assert validation.reason_code in {
        ObservabilityReasonCode.EVENT_IDENTITY_MISMATCH,
        ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE,
    }


def test_correlation_and_causation_mismatch_are_blocked() -> None:
    first = event()
    first_append = AuditJournal.empty().append(first)
    assert first_append.journal is not None
    wrong_cause = event(
        previous=first.content_identity,
        subject_identity=ContentIdentity.from_text("wrong-cause"),
        causation_id=CausationId("obs-event:wrong"),
    )
    cause_result = first_append.journal.append(wrong_cause)
    assert cause_result.reason_code is ObservabilityReasonCode.CAUSATION_INCOMPATIBLE

    other_correlation = CorrelationId(f"correlation:{ContentIdentity.from_text('other')}")
    wrong_correlation = event(
        previous=first.content_identity,
        subject_identity=ContentIdentity.from_text("wrong-correlation"),
        causation_id=CausationId(str(first.event_id)),
    )
    object.__setattr__(wrong_correlation, "correlation_id", other_correlation)
    canonical = wrong_correlation.canonical_value()
    identity = ContentIdentity.from_canonical(canonical)
    object.__setattr__(wrong_correlation, "content_identity", identity)
    object.__setattr__(wrong_correlation, "event_id", EventId(f"obs-event:{identity}"))
    correlation_result = first_append.journal.append(wrong_correlation)
    assert correlation_result.reason_code is ObservabilityReasonCode.CORRELATION_INCOMPATIBLE
