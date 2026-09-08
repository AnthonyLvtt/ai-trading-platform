from __future__ import annotations

from dataclasses import dataclass

from atp.observability.events import (
    EVENT_SCHEMA_VERSION,
    ObservabilityEvent,
    ObservabilityReasonCode,
    ObservabilityStatus,
    validate_event,
)
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class AuditValidationResult:
    status: ObservabilityStatus
    reason_code: ObservabilityReasonCode | None
    journal_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class AuditAppendResult:
    status: ObservabilityStatus
    reason_code: ObservabilityReasonCode | None
    journal: AuditJournal | None
    content_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class AuditJournal:
    events: tuple[ObservabilityEvent, ...]
    schema_version: str
    content_identity: ContentIdentity

    @classmethod
    def empty(cls) -> AuditJournal:
        return cls._from_valid_events(())

    @classmethod
    def _from_valid_events(cls, events: tuple[ObservabilityEvent, ...]) -> AuditJournal:
        identity = _journal_identity(events)
        return cls(events, EVENT_SCHEMA_VERSION, identity)

    def append(self, event: object) -> AuditAppendResult:
        current = validate_journal(self)
        if current.status is ObservabilityStatus.BLOCKED:
            return _blocked_append(current.reason_code, current.journal_identity)
        event_validation = validate_event(event)
        if event_validation.status is ObservabilityStatus.BLOCKED:
            return _blocked_append(event_validation.reason_code, event_validation.input_identity)
        assert event_validation.event is not None
        candidate = event_validation.event
        if any(existing.event_id == candidate.event_id for existing in self.events):
            return _blocked_append(
                ObservabilityReasonCode.DUPLICATE_EVENT, candidate.content_identity
            )
        previous = self.events[-1] if self.events else None
        expected_previous = None if previous is None else previous.content_identity
        if candidate.previous_event_identity != expected_previous:
            return _blocked_append(
                ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE, candidate.content_identity
            )
        if previous is None and candidate.causation_id is not None:
            return _blocked_append(
                ObservabilityReasonCode.CAUSATION_INCOMPATIBLE,
                candidate.content_identity,
            )
        if previous is not None and not _causation_is_compatible(candidate, self.events):
            return _blocked_append(
                ObservabilityReasonCode.CAUSATION_INCOMPATIBLE,
                candidate.content_identity,
            )
        if previous is not None and candidate.correlation_id != previous.correlation_id:
            return _blocked_append(
                ObservabilityReasonCode.CORRELATION_INCOMPATIBLE,
                candidate.content_identity,
            )
        if previous is not None and candidate.occurred_at < previous.occurred_at:
            return _blocked_append(
                ObservabilityReasonCode.NON_MONOTONIC_EVENT_TIME, candidate.content_identity
            )
        journal = self._from_valid_events((*self.events, candidate))
        return AuditAppendResult(
            ObservabilityStatus.ACCEPTED, None, journal, journal.content_identity
        )

    def validate(self) -> AuditValidationResult:
        return validate_journal(self)


def validate_journal(value: object) -> AuditValidationResult:
    fallback = _safe_journal_identity(value)
    if not isinstance(value, AuditJournal):
        return AuditValidationResult(
            ObservabilityStatus.BLOCKED,
            ObservabilityReasonCode.INVALID_EVENT_INPUT,
            fallback,
        )
    if value.schema_version != EVENT_SCHEMA_VERSION or not isinstance(value.events, tuple):
        return AuditValidationResult(
            ObservabilityStatus.BLOCKED,
            ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE,
            fallback,
        )
    seen: set[object] = set()
    previous: ObservabilityEvent | None = None
    for event in value.events:
        validation = validate_event(event)
        if validation.status is ObservabilityStatus.BLOCKED:
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED, validation.reason_code, fallback
            )
        assert validation.event is not None
        if event.event_id in seen:
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.DUPLICATE_EVENT,
                fallback,
            )
        expected_previous = None if previous is None else previous.content_identity
        if event.previous_event_identity != expected_previous:
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE,
                fallback,
            )
        if previous is None and event.causation_id is not None:
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.CAUSATION_INCOMPATIBLE,
                fallback,
            )
        if previous is not None and not _causation_is_compatible(event, value.events[: len(seen)]):
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.CAUSATION_INCOMPATIBLE,
                fallback,
            )
        if previous is not None and event.correlation_id != previous.correlation_id:
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.CORRELATION_INCOMPATIBLE,
                fallback,
            )
        if previous is not None and event.occurred_at < previous.occurred_at:
            return AuditValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.NON_MONOTONIC_EVENT_TIME,
                fallback,
            )
        seen.add(event.event_id)
        previous = event
    expected = _journal_identity(value.events)
    if value.content_identity != expected:
        return AuditValidationResult(
            ObservabilityStatus.BLOCKED,
            ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE,
            expected,
        )
    return AuditValidationResult(ObservabilityStatus.ACCEPTED, None, expected)


def _journal_identity(events: tuple[ObservabilityEvent, ...]) -> ContentIdentity:
    return ContentIdentity.from_canonical(
        {
            "event_content_identities": [str(event.content_identity) for event in events],
            "schema_version": EVENT_SCHEMA_VERSION,
        }
    )


def _causation_is_compatible(
    event: ObservabilityEvent, prior_events: tuple[ObservabilityEvent, ...]
) -> bool:
    if event.causation_id is None:
        return False
    cause = str(event.causation_id)
    if cause.startswith("artifact:sha256:") and len(cause) == len("artifact:sha256:") + 64:
        return all(character in "0123456789abcdef" for character in cause[-64:])
    return any(cause == str(prior.event_id) for prior in prior_events)


def _safe_journal_identity(value: object) -> ContentIdentity:
    value_type = type(value)
    return ContentIdentity.from_canonical(
        {
            "invalid_journal_type": f"{value_type.__module__}.{value_type.__qualname__}",
            "schema_version": EVENT_SCHEMA_VERSION,
        }
    )


def _blocked_append(
    reason_code: ObservabilityReasonCode | None, input_identity: ContentIdentity
) -> AuditAppendResult:
    reason = reason_code or ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE
    identity = ContentIdentity.from_canonical(
        {"input_identity": str(input_identity), "reason_code": reason.value}
    )
    return AuditAppendResult(ObservabilityStatus.BLOCKED, reason, None, identity)
