from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from atp.data.identity import DatasetId, SnapshotId, UniverseSnapshotId
from atp.data.snapshot import DatasetSnapshot
from atp.data.universe import UniverseSnapshot
from atp.shared.environment import Environment, require_active_environment
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import LogicalTime, require_utc
from atp.strategy.identity import StrategyDecisionId, StrategyEvaluationId, StrategyId


class SignalKind(StrEnum):
    LONG_ENTRY = "LONG_ENTRY"
    EXIT = "EXIT"
    NO_ACTION = "NO_ACTION"


class EvaluationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED_INPUT = "BLOCKED_INPUT"
    UNKNOWN = "UNKNOWN"


class ReasonCode(StrEnum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    DATA_CONTRACT_UNSATISFIED = "DATA_CONTRACT_UNSATISFIED"
    SNAPSHOT_INCOMPATIBLE = "SNAPSHOT_INCOMPATIBLE"
    UNIVERSE_INCOMPATIBLE = "UNIVERSE_INCOMPATIBLE"
    INVALID_CLOSE = "INVALID_CLOSE"
    NON_MONOTONIC_EVENT_TIME = "NON_MONOTONIC_EVENT_TIME"


@dataclass(frozen=True, slots=True)
class SmaCrossoverConfig:
    short_window: int
    long_window: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.short_window, int)
            or isinstance(self.short_window, bool)
            or self.short_window < 1
        ):
            raise ValidationError("short_window must be an integer greater than or equal to 1")
        if (
            not isinstance(self.long_window, int)
            or isinstance(self.long_window, bool)
            or self.long_window <= self.short_window
        ):
            raise ValidationError("long_window must be an integer greater than short_window")

    def canonical_value(self) -> dict[str, int]:
        return {"long_window": self.long_window, "short_window": self.short_window}

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class StrategyEvaluationContext:
    environment: Environment
    snapshot: DatasetSnapshot
    universe: UniverseSnapshot
    evaluation_time: LogicalTime
    symbol: str

    def __post_init__(self) -> None:
        require_active_environment(self.environment)
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValidationError("Strategy evaluation symbol must be non-empty and trimmed")


@dataclass(frozen=True, slots=True)
class UsedDataPoint:
    content_identity: ContentIdentity
    event_time: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.event_time)
        require_utc(self.available_at)

    def canonical_value(self) -> dict[str, str]:
        return {
            "available_at": self.available_at.isoformat(),
            "content_identity": str(self.content_identity),
            "event_time": self.event_time.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SignalProvenance:
    strategy_id: StrategyId
    strategy_version: str
    configuration: SmaCrossoverConfig
    environment: Environment
    dataset_id: DatasetId
    snapshot_id: SnapshotId
    snapshot_content_identity: ContentIdentity
    schema_version: str
    transformation_version: str
    lineage_content_identity: ContentIdentity
    universe_snapshot_id: UniverseSnapshotId
    universe_content_identity: ContentIdentity
    evaluation_time: LogicalTime
    symbol: str
    used_data: tuple[UsedDataPoint, ...]

    def __post_init__(self) -> None:
        if not self.strategy_version or self.strategy_version.strip() != self.strategy_version:
            raise ValidationError("strategy_version must be non-empty and trimmed")
        for field_name, value in (
            ("schema_version", self.schema_version),
            ("transformation_version", self.transformation_version),
        ):
            if not value or value.strip() != value:
                raise ValidationError(f"{field_name} must be non-empty and trimmed")
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValidationError("Strategy provenance symbol must be non-empty and trimmed")

    def canonical_value(self) -> dict[str, object]:
        return {
            "configuration": self.configuration.canonical_value(),
            "configuration_identity": str(self.configuration.content_identity),
            "dataset_id": str(self.dataset_id),
            "environment": self.environment.value,
            "evaluation_time": self.evaluation_time.value.isoformat(),
            "lineage_content_identity": str(self.lineage_content_identity),
            "schema_version": self.schema_version,
            "snapshot_content_identity": str(self.snapshot_content_identity),
            "snapshot_id": str(self.snapshot_id),
            "strategy_id": str(self.strategy_id),
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "transformation_version": self.transformation_version,
            "universe_content_identity": str(self.universe_content_identity),
            "universe_snapshot_id": str(self.universe_snapshot_id),
            "used_data": [point.canonical_value() for point in self.used_data],
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


def _evaluation_id(provenance: SignalProvenance) -> StrategyEvaluationId:
    identity = ContentIdentity.from_canonical(
        {"kind": "strategy_evaluation", "provenance": provenance.canonical_value()}
    )
    return StrategyEvaluationId(f"strategy-evaluation:{identity}")


@dataclass(frozen=True, slots=True)
class StrategySignal:
    kind: SignalKind
    provenance: SignalProvenance
    content_identity: ContentIdentity
    strategy_decision_id: StrategyDecisionId | None

    @classmethod
    def create(cls, kind: SignalKind, provenance: SignalProvenance) -> StrategySignal:
        identity = ContentIdentity.from_canonical(
            {"kind": kind.value, "provenance": provenance.canonical_value()}
        )
        decision_id = (
            None
            if kind is SignalKind.NO_ACTION
            else StrategyDecisionId(f"strategy-decision:{identity}")
        )
        return cls(
            kind=kind,
            provenance=provenance,
            content_identity=identity,
            strategy_decision_id=decision_id,
        )

    def __post_init__(self) -> None:
        expected = ContentIdentity.from_canonical(
            {"kind": self.kind.value, "provenance": self.provenance.canonical_value()}
        )
        if self.content_identity != expected:
            raise ValidationError("Strategy signal content_identity is inconsistent")
        expected_decision_id = (
            None
            if self.kind is SignalKind.NO_ACTION
            else StrategyDecisionId(f"strategy-decision:{expected}")
        )
        if self.strategy_decision_id != expected_decision_id:
            raise ValidationError("Strategy signal decision identity is inconsistent")


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    strategy_evaluation_id: StrategyEvaluationId
    status: EvaluationStatus
    provenance: SignalProvenance
    signal: StrategySignal | None
    reason_code: ReasonCode | None
    content_identity: ContentIdentity

    @classmethod
    def completed(cls, provenance: SignalProvenance, signal_kind: SignalKind) -> StrategyEvaluation:
        signal = StrategySignal.create(signal_kind, provenance)
        return cls._create(
            provenance=provenance,
            status=EvaluationStatus.COMPLETED,
            signal=signal,
            reason_code=None,
        )

    @classmethod
    def blocked(cls, provenance: SignalProvenance, reason_code: ReasonCode) -> StrategyEvaluation:
        return cls._create(
            provenance=provenance,
            status=EvaluationStatus.BLOCKED_INPUT,
            signal=None,
            reason_code=reason_code,
        )

    @classmethod
    def _create(
        cls,
        *,
        provenance: SignalProvenance,
        status: EvaluationStatus,
        signal: StrategySignal | None,
        reason_code: ReasonCode | None,
    ) -> StrategyEvaluation:
        evaluation_id = _evaluation_id(provenance)
        identity = ContentIdentity.from_canonical(
            {
                "provenance": provenance.canonical_value(),
                "reason_code": None if reason_code is None else reason_code.value,
                "signal_identity": None if signal is None else str(signal.content_identity),
                "status": status.value,
                "strategy_evaluation_id": str(evaluation_id),
            }
        )
        return cls(
            strategy_evaluation_id=evaluation_id,
            status=status,
            provenance=provenance,
            signal=signal,
            reason_code=reason_code,
            content_identity=identity,
        )

    def __post_init__(self) -> None:
        if self.strategy_evaluation_id != _evaluation_id(self.provenance):
            raise ValidationError("Strategy evaluation identity is inconsistent")
        if self.status is EvaluationStatus.COMPLETED:
            if self.signal is None or self.reason_code is not None:
                raise ValidationError("completed Strategy evaluation requires only a signal")
            if self.signal.provenance != self.provenance:
                raise ValidationError("Strategy signal and evaluation provenance must match")
        elif self.signal is not None or self.reason_code is None:
            raise ValidationError("non-completed Strategy evaluation requires only a reason")
        expected = ContentIdentity.from_canonical(
            {
                "provenance": self.provenance.canonical_value(),
                "reason_code": None if self.reason_code is None else self.reason_code.value,
                "signal_identity": None
                if self.signal is None
                else str(self.signal.content_identity),
                "status": self.status.value,
                "strategy_evaluation_id": str(self.strategy_evaluation_id),
            }
        )
        if self.content_identity != expected:
            raise ValidationError("Strategy evaluation content_identity is inconsistent")
