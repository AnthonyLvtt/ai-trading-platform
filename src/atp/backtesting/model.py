from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from atp.backtesting.identity import (
    BacktestRunId,
    SimulatedFillId,
    SimulatedOrderId,
    SimulationPolicyId,
)
from atp.data.identity import DatasetId, SnapshotId
from atp.risk.identity import RiskDecisionId
from atp.risk.model import RiskReasonCode, RiskStatus
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import require_utc
from atp.strategy.identity import StrategyDecisionId, StrategyEvaluationId


class SimulatedOrderSide(StrEnum):
    BUY_ENTRY = "BUY_ENTRY"
    SELL_EXIT = "SELL_EXIT"


class SimulatedOrderOutcome(StrEnum):
    FILLED = "FILLED"
    BLOCKED = "BLOCKED"
    UNFILLED_END_OF_REPLAY = "UNFILLED_END_OF_REPLAY"
    NO_ORDER = "NO_ORDER"


class SimulatedPositionStatus(StrEnum):
    EMPTY = "EMPTY"
    OPEN_LONG = "OPEN_LONG"


class BacktestStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class BacktestReasonCode(StrEnum):
    STEP_EXECUTED = "STEP_EXECUTED"
    STRATEGY_NO_ACTION = "STRATEGY_NO_ACTION"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_BLOCKED = "RISK_BLOCKED"
    RISK_EVIDENCE_INCOMPATIBLE = "RISK_EVIDENCE_INCOMPATIBLE"
    REPLAY_STATE_INCONSISTENT = "REPLAY_STATE_INCONSISTENT"
    DATA_SNAPSHOT_INADMISSIBLE = "DATA_SNAPSHOT_INADMISSIBLE"
    EVALUATION_BAR_INADMISSIBLE = "EVALUATION_BAR_INADMISSIBLE"
    NEXT_BAR_INADMISSIBLE = "NEXT_BAR_INADMISSIBLE"
    END_OF_REPLAY = "END_OF_REPLAY"


@dataclass(frozen=True, slots=True)
class SimulatedPositionState:
    status: SimulatedPositionStatus
    symbol: str | None

    @classmethod
    def empty(cls) -> SimulatedPositionState:
        return cls(SimulatedPositionStatus.EMPTY, None)

    @classmethod
    def open_long(cls, symbol: str) -> SimulatedPositionState:
        return cls(SimulatedPositionStatus.OPEN_LONG, symbol)

    def __post_init__(self) -> None:
        if self.status is SimulatedPositionStatus.EMPTY and self.symbol is not None:
            raise ValidationError("EMPTY simulated state cannot carry a symbol")
        if self.status is SimulatedPositionStatus.OPEN_LONG and (
            not isinstance(self.symbol, str)
            or not self.symbol
            or self.symbol.strip() != self.symbol
        ):
            raise ValidationError("OPEN_LONG simulated state requires a trimmed symbol")

    def canonical_value(self) -> dict[str, str | None]:
        return {"status": self.status.value, "symbol": self.symbol}

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class ExecutionProvenance:
    dataset_id: DatasetId
    snapshot_id: SnapshotId
    snapshot_identity: ContentIdentity
    evaluation_bar_identity: ContentIdentity
    evaluation_bar_event_time: datetime
    evaluation_time: datetime
    strategy_evaluation_id: StrategyEvaluationId
    strategy_evaluation_identity: ContentIdentity
    strategy_decision_id: StrategyDecisionId
    strategy_signal_identity: ContentIdentity
    risk_decision_id: RiskDecisionId
    risk_decision_identity: ContentIdentity
    risk_status: RiskStatus
    risk_reason_code: RiskReasonCode
    simulation_policy_id: SimulationPolicyId
    simulation_policy_version: str
    simulation_policy_identity: ContentIdentity
    symbol: str

    def __post_init__(self) -> None:
        require_utc(self.evaluation_bar_event_time)
        require_utc(self.evaluation_time)

    def canonical_value(self) -> dict[str, object]:
        return {
            "dataset_id": str(self.dataset_id),
            "evaluation_bar_event_time": self.evaluation_bar_event_time.isoformat(),
            "evaluation_bar_identity": str(self.evaluation_bar_identity),
            "evaluation_time": self.evaluation_time.isoformat(),
            "risk_decision_id": str(self.risk_decision_id),
            "risk_decision_identity": str(self.risk_decision_identity),
            "risk_reason_code": self.risk_reason_code.value,
            "risk_status": self.risk_status.value,
            "simulation_policy_id": str(self.simulation_policy_id),
            "simulation_policy_identity": str(self.simulation_policy_identity),
            "simulation_policy_version": self.simulation_policy_version,
            "snapshot_id": str(self.snapshot_id),
            "snapshot_identity": str(self.snapshot_identity),
            "strategy_decision_id": str(self.strategy_decision_id),
            "strategy_evaluation_id": str(self.strategy_evaluation_id),
            "strategy_evaluation_identity": str(self.strategy_evaluation_identity),
            "strategy_signal_identity": str(self.strategy_signal_identity),
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    simulated_order_id: SimulatedOrderId
    symbol: str
    side: SimulatedOrderSide
    strategy_evaluation_id: StrategyEvaluationId
    strategy_decision_id: StrategyDecisionId
    risk_decision_id: RiskDecisionId
    created_at: datetime
    simulation_policy_id: SimulationPolicyId
    simulation_policy_version: str
    provenance: ExecutionProvenance
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        side: SimulatedOrderSide,
        created_at: datetime,
        provenance: ExecutionProvenance,
    ) -> SimulatedOrder:
        value = {
            "created_at": created_at.isoformat(),
            "provenance": provenance.canonical_value(),
            "side": side.value,
            "symbol": provenance.symbol,
        }
        identity = ContentIdentity.from_canonical(value)
        return cls(
            simulated_order_id=SimulatedOrderId(f"simulated-order:{identity}"),
            symbol=provenance.symbol,
            side=side,
            strategy_evaluation_id=provenance.strategy_evaluation_id,
            strategy_decision_id=provenance.strategy_decision_id,
            risk_decision_id=provenance.risk_decision_id,
            created_at=created_at,
            simulation_policy_id=provenance.simulation_policy_id,
            simulation_policy_version=provenance.simulation_policy_version,
            provenance=provenance,
            content_identity=identity,
        )

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        expected = ContentIdentity.from_canonical(
            {
                "created_at": self.created_at.isoformat(),
                "provenance": self.provenance.canonical_value(),
                "side": self.side.value,
                "symbol": self.symbol,
            }
        )
        if self.content_identity != expected:
            raise ValidationError("Simulated order content identity is inconsistent")
        if self.simulated_order_id != SimulatedOrderId(f"simulated-order:{expected}"):
            raise ValidationError("Simulated order identifier is inconsistent")
        if (
            self.symbol != self.provenance.symbol
            or self.strategy_evaluation_id != self.provenance.strategy_evaluation_id
            or self.strategy_decision_id != self.provenance.strategy_decision_id
            or self.risk_decision_id != self.provenance.risk_decision_id
            or self.simulation_policy_id != self.provenance.simulation_policy_id
            or self.simulation_policy_version != self.provenance.simulation_policy_version
        ):
            raise ValidationError("Simulated order provenance is inconsistent")


@dataclass(frozen=True, slots=True)
class FillProvenance:
    order_provenance: ExecutionProvenance
    simulated_order_identity: ContentIdentity
    source_bar_identity: ContentIdentity
    source_bar_event_time: datetime
    source_bar_available_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.source_bar_event_time)
        require_utc(self.source_bar_available_at)

    def canonical_value(self) -> dict[str, object]:
        return {
            "order_provenance": self.order_provenance.canonical_value(),
            "simulated_order_identity": str(self.simulated_order_identity),
            "source_bar_available_at": self.source_bar_available_at.isoformat(),
            "source_bar_event_time": self.source_bar_event_time.isoformat(),
            "source_bar_identity": str(self.source_bar_identity),
        }


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    simulated_fill_id: SimulatedFillId
    simulated_order_id: SimulatedOrderId
    symbol: str
    side: SimulatedOrderSide
    fill_price: Decimal
    fill_time: datetime
    source_bar_identity: ContentIdentity
    simulation_policy_id: SimulationPolicyId
    simulation_policy_version: str
    provenance: FillProvenance
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        order: SimulatedOrder,
        fill_price: Decimal,
        fill_time: datetime,
        source_bar_identity: ContentIdentity,
        source_bar_event_time: datetime,
    ) -> SimulatedFill:
        provenance = FillProvenance(
            order_provenance=order.provenance,
            simulated_order_identity=order.content_identity,
            source_bar_identity=source_bar_identity,
            source_bar_event_time=source_bar_event_time,
            source_bar_available_at=fill_time,
        )
        value = {
            "fill_price": str(fill_price),
            "fill_time": fill_time.isoformat(),
            "provenance": provenance.canonical_value(),
            "side": order.side.value,
            "simulated_order_id": str(order.simulated_order_id),
            "symbol": order.symbol,
        }
        identity = ContentIdentity.from_canonical(value)
        return cls(
            simulated_fill_id=SimulatedFillId(f"simulated-fill:{identity}"),
            simulated_order_id=order.simulated_order_id,
            symbol=order.symbol,
            side=order.side,
            fill_price=fill_price,
            fill_time=fill_time,
            source_bar_identity=source_bar_identity,
            simulation_policy_id=order.simulation_policy_id,
            simulation_policy_version=order.simulation_policy_version,
            provenance=provenance,
            content_identity=identity,
        )

    def __post_init__(self) -> None:
        require_utc(self.fill_time)
        if not self.fill_price.is_finite() or self.fill_price <= 0:
            raise ValidationError("Simulated fill price must be finite and positive")
        expected = ContentIdentity.from_canonical(
            {
                "fill_price": str(self.fill_price),
                "fill_time": self.fill_time.isoformat(),
                "provenance": self.provenance.canonical_value(),
                "side": self.side.value,
                "simulated_order_id": str(self.simulated_order_id),
                "symbol": self.symbol,
            }
        )
        if self.content_identity != expected:
            raise ValidationError("Simulated fill content identity is inconsistent")
        if self.simulated_fill_id != SimulatedFillId(f"simulated-fill:{expected}"):
            raise ValidationError("Simulated fill identifier is inconsistent")
        if (
            self.source_bar_identity != self.provenance.source_bar_identity
            or self.fill_time != self.provenance.source_bar_available_at
            or self.simulation_policy_id != self.provenance.order_provenance.simulation_policy_id
            or self.simulation_policy_version
            != self.provenance.order_provenance.simulation_policy_version
        ):
            raise ValidationError("Simulated fill provenance is inconsistent")


@dataclass(frozen=True, slots=True)
class ReplayStepResult:
    outcome: SimulatedOrderOutcome
    reason_code: BacktestReasonCode
    risk_status: RiskStatus
    risk_reason_code: RiskReasonCode
    order: SimulatedOrder | None
    fill: SimulatedFill | None
    position_before: SimulatedPositionState
    position_after: SimulatedPositionState

    def __post_init__(self) -> None:
        if self.outcome is SimulatedOrderOutcome.NO_ORDER and (
            self.order is not None or self.fill is not None
        ):
            raise ValidationError("NO_ORDER cannot carry an order or fill")
        if self.outcome is SimulatedOrderOutcome.FILLED and (
            self.order is None or self.fill is None
        ):
            raise ValidationError("FILLED requires exactly one order and fill")
        if (
            self.outcome
            in {
                SimulatedOrderOutcome.BLOCKED,
                SimulatedOrderOutcome.UNFILLED_END_OF_REPLAY,
            }
            and self.fill is not None
        ):
            raise ValidationError("A blocked or unfilled order cannot carry a fill")
        if self.outcome is SimulatedOrderOutcome.UNFILLED_END_OF_REPLAY and self.order is None:
            raise ValidationError("UNFILLED_END_OF_REPLAY requires an approved order")
        if self.fill is not None and (
            self.order is None or self.fill.simulated_order_id != self.order.simulated_order_id
        ):
            raise ValidationError("Simulated fill must reference its order")

    def canonical_value(self) -> dict[str, object]:
        return {
            "fill_identity": None if self.fill is None else str(self.fill.content_identity),
            "order_identity": None if self.order is None else str(self.order.content_identity),
            "outcome": self.outcome.value,
            "position_after": self.position_after.canonical_value(),
            "position_before": self.position_before.canonical_value(),
            "reason_code": self.reason_code.value,
            "risk_reason_code": self.risk_reason_code.value,
            "risk_status": self.risk_status.value,
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    backtest_run_id: BacktestRunId
    status: BacktestStatus
    reason_code: BacktestReasonCode | None
    input_identity: ContentIdentity
    simulation_policy_identity: ContentIdentity
    number_of_strategy_evaluations: int
    number_of_risk_approved: int
    number_of_risk_rejected: int
    number_of_risk_blocked: int
    number_of_orders: int
    number_of_fills: int
    terminal_simulated_position_state: SimulatedPositionState
    steps: tuple[ReplayStepResult, ...]
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        status: BacktestStatus,
        reason_code: BacktestReasonCode | None,
        input_identity: ContentIdentity,
        simulation_policy_identity: ContentIdentity,
        steps: tuple[ReplayStepResult, ...],
        terminal_state: SimulatedPositionState,
    ) -> BacktestResult:
        canonical = {
            "input_identity": str(input_identity),
            "simulation_policy_identity": str(simulation_policy_identity),
            "reason_code": None if reason_code is None else reason_code.value,
            "status": status.value,
            "steps": [step.canonical_value() for step in steps],
            "terminal_state": terminal_state.canonical_value(),
        }
        identity = ContentIdentity.from_canonical(canonical)
        return cls(
            backtest_run_id=BacktestRunId(f"backtest-run:{identity}"),
            status=status,
            reason_code=reason_code,
            input_identity=input_identity,
            simulation_policy_identity=simulation_policy_identity,
            number_of_strategy_evaluations=len(steps),
            number_of_risk_approved=sum(step.risk_status is RiskStatus.APPROVED for step in steps),
            number_of_risk_rejected=sum(step.risk_status is RiskStatus.REJECTED for step in steps),
            number_of_risk_blocked=sum(step.risk_status is RiskStatus.BLOCKED for step in steps),
            number_of_orders=sum(step.order is not None for step in steps),
            number_of_fills=sum(step.fill is not None for step in steps),
            terminal_simulated_position_state=terminal_state,
            steps=steps,
            content_identity=identity,
        )

    def __post_init__(self) -> None:
        canonical = {
            "input_identity": str(self.input_identity),
            "reason_code": None if self.reason_code is None else self.reason_code.value,
            "simulation_policy_identity": str(self.simulation_policy_identity),
            "status": self.status.value,
            "steps": [step.canonical_value() for step in self.steps],
            "terminal_state": self.terminal_simulated_position_state.canonical_value(),
        }
        expected = ContentIdentity.from_canonical(canonical)
        if self.content_identity != expected or self.backtest_run_id != BacktestRunId(
            f"backtest-run:{expected}"
        ):
            raise ValidationError("Backtest result identity is inconsistent")
        expected_counts = (
            len(self.steps),
            sum(step.risk_status is RiskStatus.APPROVED for step in self.steps),
            sum(step.risk_status is RiskStatus.REJECTED for step in self.steps),
            sum(step.risk_status is RiskStatus.BLOCKED for step in self.steps),
            sum(step.order is not None for step in self.steps),
            sum(step.fill is not None for step in self.steps),
        )
        actual_counts = (
            self.number_of_strategy_evaluations,
            self.number_of_risk_approved,
            self.number_of_risk_rejected,
            self.number_of_risk_blocked,
            self.number_of_orders,
            self.number_of_fills,
        )
        if actual_counts != expected_counts:
            raise ValidationError("Backtest result counters are inconsistent")
        if (self.status is BacktestStatus.COMPLETED) != (self.reason_code is None):
            raise ValidationError("Backtest result status and reason are inconsistent")
