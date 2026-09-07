from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from types import MappingProxyType

from atp.shared.environment import Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import CausationId, ContentIdentity, CorrelationId, EventId
from atp.shared.time import require_utc

EVENT_SCHEMA_VERSION = "1.0"


class EventType(StrEnum):
    DATA_SNAPSHOT_ACCEPTED = "DATA_SNAPSHOT_ACCEPTED"
    DATA_SNAPSHOT_BLOCKED = "DATA_SNAPSHOT_BLOCKED"
    STRATEGY_EVALUATED = "STRATEGY_EVALUATED"
    RISK_DECISION_PRODUCED = "RISK_DECISION_PRODUCED"
    SIMULATED_ORDER_CREATED = "SIMULATED_ORDER_CREATED"
    SIMULATED_FILL_PRODUCED = "SIMULATED_FILL_PRODUCED"
    BACKTEST_COMPLETED = "BACKTEST_COMPLETED"
    BACKTEST_BLOCKED = "BACKTEST_BLOCKED"
    ACCOUNTING_ENTRY_APPLIED = "ACCOUNTING_ENTRY_APPLIED"
    ACCOUNTING_REPLAY_COMPLETED = "ACCOUNTING_REPLAY_COMPLETED"
    ACCOUNTING_REPLAY_BLOCKED = "ACCOUNTING_REPLAY_BLOCKED"
    ACCOUNTING_VALUATION_PRODUCED = "ACCOUNTING_VALUATION_PRODUCED"
    ACCOUNTING_VALUATION_BLOCKED = "ACCOUNTING_VALUATION_BLOCKED"


class EventModule(StrEnum):
    DATA = "DATA"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    BACKTESTING = "BACKTESTING"
    ACCOUNTING = "ACCOUNTING"
    OBSERVABILITY = "OBSERVABILITY"


class EventCategory(StrEnum):
    DOMAIN = "DOMAIN"
    CONTROL = "CONTROL"
    AUDIT = "AUDIT"


class EventSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class EventClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SECRET = "SECRET"


class ObservabilityStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"


class ObservabilityReasonCode(StrEnum):
    INVALID_EVENT_INPUT = "INVALID_EVENT_INPUT"
    INVALID_EVENT_TYPE = "INVALID_EVENT_TYPE"
    INVALID_EVENT_SCHEMA_VERSION = "INVALID_EVENT_SCHEMA_VERSION"
    INVALID_EVENT_TIME = "INVALID_EVENT_TIME"
    INVALID_SUBJECT_REFERENCE = "INVALID_SUBJECT_REFERENCE"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    SENSITIVE_DATA_DETECTED = "SENSITIVE_DATA_DETECTED"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    EVENT_IDENTITY_MISMATCH = "EVENT_IDENTITY_MISMATCH"
    EVENT_INTEGRITY_FAILURE = "EVENT_INTEGRITY_FAILURE"
    CAUSATION_INCOMPATIBLE = "CAUSATION_INCOMPATIBLE"
    CORRELATION_INCOMPATIBLE = "CORRELATION_INCOMPATIBLE"
    NON_MONOTONIC_EVENT_TIME = "NON_MONOTONIC_EVENT_TIME"


SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "api_key",
        "apikey",
        "api_secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "private_key",
        "seed",
        "mnemonic",
        "connection_string",
    }
)

_EVENT_CONTRACTS: dict[EventType, tuple[EventModule, str, frozenset[str]]] = {
    EventType.DATA_SNAPSHOT_ACCEPTED: (
        EventModule.DATA,
        "DatasetSnapshot",
        frozenset({"quality", "freshness", "gap_status", "dataset_id", "snapshot_id"}),
    ),
    EventType.DATA_SNAPSHOT_BLOCKED: (
        EventModule.DATA,
        "DatasetSnapshot",
        frozenset({"reason_code", "status"}),
    ),
    EventType.STRATEGY_EVALUATED: (
        EventModule.STRATEGY,
        "StrategyEvaluation",
        frozenset(
            {
                "evaluation_status",
                "signal_kind",
                "strategy_id",
                "strategy_version",
                "reason_code",
            }
        ),
    ),
    EventType.RISK_DECISION_PRODUCED: (
        EventModule.RISK,
        "RiskProcessingResult",
        frozenset({"risk_status", "risk_reason_code", "risk_policy_id", "risk_policy_version"}),
    ),
    EventType.SIMULATED_ORDER_CREATED: (
        EventModule.BACKTESTING,
        "SimulatedOrder",
        frozenset({"side", "symbol"}),
    ),
    EventType.SIMULATED_FILL_PRODUCED: (
        EventModule.BACKTESTING,
        "SimulatedFill",
        frozenset({"fill_price", "side", "source_bar_identity", "symbol"}),
    ),
    EventType.BACKTEST_COMPLETED: (
        EventModule.BACKTESTING,
        "BacktestResult",
        frozenset({"number_of_fills", "number_of_orders", "reason_code", "status"}),
    ),
    EventType.BACKTEST_BLOCKED: (
        EventModule.BACKTESTING,
        "BacktestResult",
        frozenset({"number_of_fills", "number_of_orders", "reason_code", "status"}),
    ),
    EventType.ACCOUNTING_ENTRY_APPLIED: (
        EventModule.ACCOUNTING,
        "AccountingEntry",
        frozenset({"cash_delta", "realized_pnl_delta", "side", "symbol"}),
    ),
    EventType.ACCOUNTING_REPLAY_COMPLETED: (
        EventModule.ACCOUNTING,
        "AccountingReplayResult",
        frozenset({"ledger_entries", "reason_code", "status"}),
    ),
    EventType.ACCOUNTING_REPLAY_BLOCKED: (
        EventModule.ACCOUNTING,
        "AccountingReplayResult",
        frozenset({"ledger_entries", "reason_code", "status"}),
    ),
    EventType.ACCOUNTING_VALUATION_PRODUCED: (
        EventModule.ACCOUNTING,
        "AccountingValuation",
        frozenset({"equity", "reason_code", "status"}),
    ),
    EventType.ACCOUNTING_VALUATION_BLOCKED: (
        EventModule.ACCOUNTING,
        "AccountingValuation",
        frozenset({"equity", "reason_code", "status"}),
    ),
}


class _InvalidEvent(ValueError):
    def __init__(self, reason_code: ObservabilityReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


def _safe_type_name(value: object) -> str:
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _require_trimmed(value: object, reason: ObservabilityReasonCode) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _InvalidEvent(reason)
    return value


def _freeze_payload(value: object, *, key: str | None = None) -> object:
    if key is not None and key.casefold() in SENSITIVE_KEYS:
        raise _InvalidEvent(ObservabilityReasonCode.SENSITIVE_DATA_DETECTED)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
        return str(value)
    if isinstance(value, datetime):
        try:
            require_utc(value)
        except ValidationError as exc:
            raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD) from exc
        return value.isoformat()
    if isinstance(value, ContentIdentity):
        return str(value)
    if isinstance(value, Enum):
        return _freeze_payload(value.value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or not raw_key or raw_key.strip() != raw_key:
                raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
            normalized[raw_key] = _freeze_payload(item, key=raw_key)
        return MappingProxyType(dict(sorted(normalized.items())))
    if isinstance(value, tuple | list):
        return tuple(_freeze_payload(item) for item in value)
    raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)


def _payload_canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _payload_canonical(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_payload_canonical(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    event_id: EventId
    event_type: EventType
    schema_version: str
    occurred_at: datetime
    environment: Environment
    module: EventModule
    category: EventCategory
    severity: EventSeverity
    correlation_id: CorrelationId
    causation_id: CausationId | None
    subject_type: str
    subject_id: str
    subject_content_identity: ContentIdentity
    payload: Mapping[str, object]
    previous_event_identity: ContentIdentity | None
    content_identity: ContentIdentity
    classification: EventClassification = EventClassification.INTERNAL

    @classmethod
    def create(
        cls,
        *,
        event_type: EventType,
        occurred_at: datetime,
        environment: Environment,
        module: EventModule,
        category: EventCategory,
        severity: EventSeverity,
        correlation_id: CorrelationId,
        causation_id: CausationId | None,
        subject_type: str,
        subject_id: str,
        subject_content_identity: ContentIdentity,
        payload: Mapping[str, object],
        previous_event_identity: ContentIdentity | None,
        schema_version: str = EVENT_SCHEMA_VERSION,
        classification: EventClassification = EventClassification.INTERNAL,
    ) -> ObservabilityEvent:
        canonical, frozen_payload = _validated_components(
            event_type=event_type,
            schema_version=schema_version,
            occurred_at=occurred_at,
            environment=environment,
            module=module,
            category=category,
            severity=severity,
            correlation_id=correlation_id,
            causation_id=causation_id,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_content_identity=subject_content_identity,
            payload=payload,
            previous_event_identity=previous_event_identity,
            classification=classification,
        )
        identity = ContentIdentity.from_canonical(canonical)
        return cls(
            EventId(f"obs-event:{identity}"),
            event_type,
            schema_version,
            occurred_at,
            environment,
            module,
            category,
            severity,
            correlation_id,
            causation_id,
            subject_type,
            subject_id,
            subject_content_identity,
            frozen_payload,
            previous_event_identity,
            identity,
            classification,
        )

    def canonical_value(self) -> dict[str, object]:
        canonical, _ = _validated_components(
            event_type=self.event_type,
            schema_version=self.schema_version,
            occurred_at=self.occurred_at,
            environment=self.environment,
            module=self.module,
            category=self.category,
            severity=self.severity,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            subject_content_identity=self.subject_content_identity,
            payload=self.payload,
            previous_event_identity=self.previous_event_identity,
            classification=self.classification,
        )
        return canonical

    def __post_init__(self) -> None:
        try:
            expected = ContentIdentity.from_canonical(self.canonical_value())
        except (_InvalidEvent, AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise ValidationError("invalid Observability event") from exc
        if self.content_identity != expected:
            raise ValidationError("Observability event content identity is inconsistent")
        if self.event_id != EventId(f"obs-event:{expected}"):
            raise ValidationError("Observability event identifier is inconsistent")


@dataclass(frozen=True, slots=True)
class EventValidationResult:
    status: ObservabilityStatus
    reason_code: ObservabilityReasonCode | None
    event: ObservabilityEvent | None
    input_identity: ContentIdentity


def build_event(**values: object) -> EventValidationResult:
    """Fail-closed public construction boundary for runtime event evidence."""
    try:
        event = ObservabilityEvent.create(**values)  # type: ignore[arg-type]
    except _InvalidEvent as exc:
        return _blocked_event_result(exc.reason_code, values)
    except Exception:  # noqa: BLE001 - runtime trust boundary must fail closed
        return _blocked_event_result(ObservabilityReasonCode.INVALID_EVENT_INPUT, values)
    return EventValidationResult(ObservabilityStatus.ACCEPTED, None, event, event.content_identity)


def validate_event(value: object) -> EventValidationResult:
    """Revalidate identity and structure without trusting dataclass type hints."""
    if not isinstance(value, ObservabilityEvent):
        return _blocked_event_result(ObservabilityReasonCode.INVALID_EVENT_INPUT, {"event": value})
    try:
        expected = ContentIdentity.from_canonical(value.canonical_value())
        if value.content_identity != expected:
            return EventValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE,
                None,
                expected,
            )
        if value.event_id != EventId(f"obs-event:{expected}"):
            return EventValidationResult(
                ObservabilityStatus.BLOCKED,
                ObservabilityReasonCode.EVENT_IDENTITY_MISMATCH,
                None,
                expected,
            )
    except _InvalidEvent as exc:
        return _blocked_event_result(exc.reason_code, {"event": value})
    except Exception:  # noqa: BLE001 - runtime trust boundary must fail closed
        return _blocked_event_result(
            ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE, {"event": value}
        )
    return EventValidationResult(ObservabilityStatus.ACCEPTED, None, value, expected)


def invalid_event_result(value: object) -> EventValidationResult:
    """Return a deterministic blocked result without rendering an external value."""
    return _blocked_event_result(ObservabilityReasonCode.INVALID_EVENT_INPUT, {"input": value})


def _validated_components(
    *,
    event_type: object,
    schema_version: object,
    occurred_at: object,
    environment: object,
    module: object,
    category: object,
    severity: object,
    correlation_id: object,
    causation_id: object,
    subject_type: object,
    subject_id: object,
    subject_content_identity: object,
    payload: object,
    previous_event_identity: object,
    classification: object,
) -> tuple[dict[str, object], Mapping[str, object]]:
    if not isinstance(event_type, EventType):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_TYPE)
    if schema_version != EVENT_SCHEMA_VERSION:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_SCHEMA_VERSION)
    if not isinstance(occurred_at, datetime):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_TIME)
    try:
        require_utc(occurred_at)
    except ValidationError as exc:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_TIME) from exc
    if not isinstance(environment, Environment):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_INPUT)
    if not isinstance(module, EventModule) or not isinstance(category, EventCategory):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_INPUT)
    if not isinstance(severity, EventSeverity):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_INPUT)
    if not isinstance(correlation_id, CorrelationId):
        raise _InvalidEvent(ObservabilityReasonCode.CORRELATION_INCOMPATIBLE)
    if causation_id is not None and not isinstance(causation_id, CausationId):
        raise _InvalidEvent(ObservabilityReasonCode.CAUSATION_INCOMPATIBLE)
    subject_type_value = _require_trimmed(
        subject_type, ObservabilityReasonCode.INVALID_SUBJECT_REFERENCE
    )
    subject_id_value = _require_trimmed(
        subject_id, ObservabilityReasonCode.INVALID_SUBJECT_REFERENCE
    )
    if not isinstance(subject_content_identity, ContentIdentity):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_SUBJECT_REFERENCE)
    if previous_event_identity is not None and not isinstance(
        previous_event_identity, ContentIdentity
    ):
        raise _InvalidEvent(ObservabilityReasonCode.EVENT_INTEGRITY_FAILURE)
    if classification is not EventClassification.INTERNAL:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_INPUT)
    if not isinstance(payload, Mapping):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    frozen_payload = _freeze_payload(payload)
    if not isinstance(frozen_payload, Mapping):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    expected_module, expected_subject, expected_fields = _EVENT_CONTRACTS[event_type]
    if module is not expected_module:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_EVENT_INPUT)
    if subject_type_value != expected_subject:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_SUBJECT_REFERENCE)
    if frozenset(frozen_payload) != expected_fields:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    _validate_event_semantics(event_type, category, severity, frozen_payload)
    canonical = {
        "category": category.value,
        "causation_id": None if causation_id is None else str(causation_id),
        "classification": classification.value,
        "correlation_id": str(correlation_id),
        "environment": environment.value,
        "event_type": event_type.value,
        "module": module.value,
        "occurred_at": occurred_at.isoformat(),
        "payload": _payload_canonical(frozen_payload),
        "previous_event_identity": None
        if previous_event_identity is None
        else str(previous_event_identity),
        "schema_version": schema_version,
        "severity": severity.value,
        "subject_content_identity": str(subject_content_identity),
        "subject_id": subject_id_value,
        "subject_type": subject_type_value,
    }
    return canonical, frozen_payload


def _validate_event_semantics(
    event_type: EventType,
    category: EventCategory,
    severity: EventSeverity,
    payload: Mapping[str, object],
) -> None:
    successful_domain_events = {
        EventType.DATA_SNAPSHOT_ACCEPTED,
        EventType.SIMULATED_ORDER_CREATED,
        EventType.SIMULATED_FILL_PRODUCED,
        EventType.BACKTEST_COMPLETED,
        EventType.ACCOUNTING_ENTRY_APPLIED,
        EventType.ACCOUNTING_REPLAY_COMPLETED,
        EventType.ACCOUNTING_VALUATION_PRODUCED,
    }
    blocked_control_events = {
        EventType.DATA_SNAPSHOT_BLOCKED,
        EventType.BACKTEST_BLOCKED,
        EventType.ACCOUNTING_REPLAY_BLOCKED,
        EventType.ACCOUNTING_VALUATION_BLOCKED,
    }
    if event_type in successful_domain_events and (
        category is not EventCategory.DOMAIN or severity is not EventSeverity.INFO
    ):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    if event_type in blocked_control_events and (
        category is not EventCategory.CONTROL or severity is not EventSeverity.ERROR
    ):
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    if event_type is EventType.STRATEGY_EVALUATED:
        expected = (
            EventSeverity.INFO
            if payload["evaluation_status"] == "COMPLETED"
            else EventSeverity.ERROR
        )
        if category is not EventCategory.DOMAIN or severity is not expected:
            raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    if event_type is EventType.RISK_DECISION_PRODUCED:
        status = payload["risk_status"]
        expected = (
            EventSeverity.ERROR
            if status == "BLOCKED"
            else EventSeverity.WARNING
            if status == "REJECTED"
            else EventSeverity.INFO
        )
        if category is not EventCategory.CONTROL or severity is not expected:
            raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)
    expected_status = {
        EventType.DATA_SNAPSHOT_BLOCKED: "BLOCKED",
        EventType.BACKTEST_COMPLETED: "COMPLETED",
        EventType.BACKTEST_BLOCKED: "BLOCKED",
        EventType.ACCOUNTING_REPLAY_COMPLETED: "COMPLETED",
        EventType.ACCOUNTING_REPLAY_BLOCKED: "BLOCKED",
        EventType.ACCOUNTING_VALUATION_PRODUCED: "COMPLETED",
        EventType.ACCOUNTING_VALUATION_BLOCKED: "BLOCKED",
    }.get(event_type)
    if expected_status is not None and payload["status"] != expected_status:
        raise _InvalidEvent(ObservabilityReasonCode.INVALID_PAYLOAD)


def _blocked_event_result(
    reason_code: ObservabilityReasonCode, values: Mapping[str, object]
) -> EventValidationResult:
    safe_shape = {
        key: _safe_type_name(value) for key, value in sorted(values.items()) if isinstance(key, str)
    }
    identity = ContentIdentity.from_canonical(
        {"reason_code": reason_code.value, "runtime_shape": safe_shape}
    )
    return EventValidationResult(ObservabilityStatus.BLOCKED, reason_code, None, identity)


DomainEvent = ObservabilityEvent
