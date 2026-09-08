from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from atp.accounting.model import (
    AccountingEntry,
    AccountingExecution,
    AccountingReplayInput,
    AccountingReplayResult,
    AccountingStatus,
    AccountingValuation,
)
from atp.backtesting.engine import BacktestInput
from atp.backtesting.model import BacktestResult, BacktestStatus, SimulatedFill, SimulatedOrder
from atp.data.snapshot import DatasetSnapshot
from atp.observability.events import (
    EventCategory,
    EventModule,
    EventSeverity,
    EventType,
    EventValidationResult,
    build_event,
    invalid_event_result,
)
from atp.risk.model import RiskProcessingResult, RiskStatus
from atp.shared.environment import Environment
from atp.shared.identity import CausationId, ContentIdentity, CorrelationId
from atp.strategy.model import EvaluationStatus, StrategyEvaluation


@dataclass(frozen=True, slots=True)
class ObservationContext:
    correlation_id: CorrelationId
    causation_id: CausationId | None = None
    previous_event_identity: ContentIdentity | None = None


def observe_data_snapshot(
    snapshot: object,
    *,
    accepted: bool,
    reason_code: str | None,
    context: ObservationContext,
) -> EventValidationResult:
    if not _valid_artifact(snapshot, DatasetSnapshot):
        return invalid_event_result(snapshot)
    assert isinstance(snapshot, DatasetSnapshot)
    event_type = EventType.DATA_SNAPSHOT_ACCEPTED if accepted else EventType.DATA_SNAPSHOT_BLOCKED
    payload: Mapping[str, object] = (
        {
            "dataset_id": str(snapshot.dataset_id),
            "freshness": snapshot.freshness,
            "gap_status": snapshot.gap_status,
            "quality": snapshot.quality,
            "snapshot_id": str(snapshot.snapshot_id),
        }
        if accepted
        else {"reason_code": reason_code, "status": "BLOCKED"}
    )
    return _build(
        event_type=event_type,
        occurred_at=snapshot.created_at,
        environment=snapshot.environment,
        module=EventModule.DATA,
        category=EventCategory.DOMAIN if accepted else EventCategory.CONTROL,
        severity=EventSeverity.INFO if accepted else EventSeverity.ERROR,
        subject_type="DatasetSnapshot",
        subject_id=str(snapshot.snapshot_id),
        subject_identity=snapshot.content_identity,
        payload=payload,
        context=context,
    )


def observe_strategy(evaluation: object, *, context: ObservationContext) -> EventValidationResult:
    if not _valid_artifact(evaluation, StrategyEvaluation):
        return invalid_event_result(evaluation)
    assert isinstance(evaluation, StrategyEvaluation)
    signal = evaluation.signal
    return _build(
        event_type=EventType.STRATEGY_EVALUATED,
        occurred_at=evaluation.provenance.evaluation_time.value,
        environment=evaluation.provenance.environment,
        module=EventModule.STRATEGY,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO
        if evaluation.status is EvaluationStatus.COMPLETED
        else EventSeverity.ERROR,
        subject_type="StrategyEvaluation",
        subject_id=str(evaluation.strategy_evaluation_id),
        subject_identity=evaluation.content_identity,
        payload={
            "evaluation_status": evaluation.status,
            "reason_code": evaluation.reason_code,
            "signal_kind": None if signal is None else signal.kind,
            "strategy_id": str(evaluation.provenance.strategy_id),
            "strategy_version": evaluation.provenance.strategy_version,
        },
        context=context,
    )


def observe_risk(
    result: object,
    *,
    strategy_evaluation: object,
    context: ObservationContext,
) -> EventValidationResult:
    if not _valid_artifact(result, RiskProcessingResult) or not _valid_artifact(
        strategy_evaluation, StrategyEvaluation
    ):
        return invalid_event_result(result)
    assert isinstance(result, RiskProcessingResult)
    assert isinstance(strategy_evaluation, StrategyEvaluation)
    signal_identity = (
        None if strategy_evaluation.signal is None else strategy_evaluation.signal.content_identity
    )
    if (
        result.provenance.environment != strategy_evaluation.provenance.environment.value
        or result.provenance.strategy_evaluation_id != strategy_evaluation.strategy_evaluation_id
        or result.provenance.strategy_evaluation_identity != strategy_evaluation.content_identity
        or result.provenance.strategy_signal_identity != signal_identity
    ):
        return invalid_event_result(result)
    subject_id = (
        str(result.risk_decision_id)
        if result.risk_decision_id is not None
        else str(result.provenance.strategy_evaluation_id)
    )
    severity = (
        EventSeverity.ERROR
        if result.status is RiskStatus.BLOCKED
        else EventSeverity.WARNING
        if result.status is RiskStatus.REJECTED
        else EventSeverity.INFO
    )
    return _build(
        event_type=EventType.RISK_DECISION_PRODUCED,
        occurred_at=strategy_evaluation.provenance.evaluation_time.value,
        environment=strategy_evaluation.provenance.environment,
        module=EventModule.RISK,
        category=EventCategory.CONTROL,
        severity=severity,
        subject_type="RiskProcessingResult",
        subject_id=subject_id,
        subject_identity=result.content_identity,
        payload={
            "risk_policy_id": str(result.provenance.risk_policy_id),
            "risk_policy_version": result.provenance.risk_policy_version,
            "risk_reason_code": result.reason_code,
            "risk_status": result.status,
        },
        context=context,
    )


def observe_simulated_order(
    order: object, *, environment: Environment, context: ObservationContext
) -> EventValidationResult:
    if not _valid_artifact(order, SimulatedOrder):
        return invalid_event_result(order)
    assert isinstance(order, SimulatedOrder)
    return _build(
        event_type=EventType.SIMULATED_ORDER_CREATED,
        occurred_at=order.created_at,
        environment=environment,
        module=EventModule.BACKTESTING,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        subject_type="SimulatedOrder",
        subject_id=str(order.simulated_order_id),
        subject_identity=order.content_identity,
        payload={"side": order.side, "symbol": order.symbol},
        context=context,
    )


def observe_simulated_fill(
    fill: object, *, environment: Environment, context: ObservationContext
) -> EventValidationResult:
    if not _valid_artifact(fill, SimulatedFill):
        return invalid_event_result(fill)
    assert isinstance(fill, SimulatedFill)
    return _build(
        event_type=EventType.SIMULATED_FILL_PRODUCED,
        occurred_at=fill.fill_time,
        environment=environment,
        module=EventModule.BACKTESTING,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        subject_type="SimulatedFill",
        subject_id=str(fill.simulated_fill_id),
        subject_identity=fill.content_identity,
        payload={
            "fill_price": fill.fill_price,
            "side": fill.side,
            "source_bar_identity": fill.source_bar_identity,
            "symbol": fill.symbol,
        },
        context=context,
    )


def observe_backtest(
    result: object,
    *,
    replay_input: object,
    context: ObservationContext,
) -> EventValidationResult:
    if not _valid_artifact(result, BacktestResult) or not _valid_backtest_input(replay_input):
        return invalid_event_result(result)
    assert isinstance(result, BacktestResult)
    assert isinstance(replay_input, BacktestInput)
    if result.input_identity != replay_input.content_identity or len(result.steps) > len(
        replay_input.steps
    ):
        return invalid_event_result(result)
    occurred_at = _backtest_occurred_at(result, replay_input)
    if occurred_at is None:
        return invalid_event_result(result)
    completed = result.status is BacktestStatus.COMPLETED
    return _build(
        event_type=EventType.BACKTEST_COMPLETED if completed else EventType.BACKTEST_BLOCKED,
        occurred_at=occurred_at,
        environment=replay_input.snapshot.environment,
        module=EventModule.BACKTESTING,
        category=EventCategory.DOMAIN if completed else EventCategory.CONTROL,
        severity=EventSeverity.INFO if completed else EventSeverity.ERROR,
        subject_type="BacktestResult",
        subject_id=str(result.backtest_run_id),
        subject_identity=result.content_identity,
        payload={
            "number_of_fills": result.number_of_fills,
            "number_of_orders": result.number_of_orders,
            "reason_code": result.reason_code,
            "status": result.status,
        },
        context=context,
    )


def observe_accounting_entry(
    entry: object, *, environment: Environment, context: ObservationContext
) -> EventValidationResult:
    if not _valid_artifact(entry, AccountingEntry):
        return invalid_event_result(entry)
    assert isinstance(entry, AccountingEntry)
    return _build(
        event_type=EventType.ACCOUNTING_ENTRY_APPLIED,
        occurred_at=entry.provenance.fill_time,
        environment=environment,
        module=EventModule.ACCOUNTING,
        category=EventCategory.DOMAIN,
        severity=EventSeverity.INFO,
        subject_type="AccountingEntry",
        subject_id=str(entry.accounting_entry_id),
        subject_identity=entry.content_identity,
        payload={
            "cash_delta": entry.cash_delta,
            "realized_pnl_delta": entry.realized_pnl_delta,
            "side": entry.provenance.side,
            "symbol": entry.provenance.symbol,
        },
        context=context,
    )


def observe_accounting_replay(
    result: object,
    *,
    replay_input: object,
    environment: Environment,
    context: ObservationContext,
) -> EventValidationResult:
    if not _valid_artifact(result, AccountingReplayResult) or not _valid_accounting_input(
        replay_input
    ):
        return invalid_event_result(result)
    assert isinstance(result, AccountingReplayResult)
    assert isinstance(replay_input, AccountingReplayInput)
    if result.input_identity != replay_input.content_identity:
        return invalid_event_result(result)
    occurred_at = _accounting_replay_occurred_at(result, replay_input)
    if occurred_at is None:
        return invalid_event_result(result)
    completed = result.status is AccountingStatus.COMPLETED
    return _build(
        event_type=EventType.ACCOUNTING_REPLAY_COMPLETED
        if completed
        else EventType.ACCOUNTING_REPLAY_BLOCKED,
        occurred_at=occurred_at,
        environment=environment,
        module=EventModule.ACCOUNTING,
        category=EventCategory.DOMAIN if completed else EventCategory.CONTROL,
        severity=EventSeverity.INFO if completed else EventSeverity.ERROR,
        subject_type="AccountingReplayResult",
        subject_id=str(result.accounting_replay_id),
        subject_identity=result.content_identity,
        payload={
            "ledger_entries": len(result.ledger),
            "reason_code": result.reason_code,
            "status": result.status,
        },
        context=context,
    )


def observe_accounting_valuation(
    valuation: object, *, environment: Environment, context: ObservationContext
) -> EventValidationResult:
    if not _valid_artifact(valuation, AccountingValuation):
        return invalid_event_result(valuation)
    assert isinstance(valuation, AccountingValuation)
    completed = valuation.status is AccountingStatus.COMPLETED
    return _build(
        event_type=EventType.ACCOUNTING_VALUATION_PRODUCED
        if completed
        else EventType.ACCOUNTING_VALUATION_BLOCKED,
        occurred_at=valuation.valuation_time,
        environment=environment,
        module=EventModule.ACCOUNTING,
        category=EventCategory.DOMAIN if completed else EventCategory.CONTROL,
        severity=EventSeverity.INFO if completed else EventSeverity.ERROR,
        subject_type="AccountingValuation",
        subject_id=str(valuation.accounting_valuation_id),
        subject_identity=valuation.content_identity,
        payload={
            "equity": valuation.equity,
            "reason_code": valuation.reason_code,
            "status": valuation.status,
        },
        context=context,
    )


def _build(
    *,
    event_type: EventType,
    occurred_at: datetime,
    environment: Environment,
    module: EventModule,
    category: EventCategory,
    severity: EventSeverity,
    subject_type: str,
    subject_id: str,
    subject_identity: ContentIdentity,
    payload: Mapping[str, object],
    context: ObservationContext,
) -> EventValidationResult:
    return build_event(
        event_type=event_type,
        occurred_at=occurred_at,
        environment=environment,
        module=module,
        category=category,
        severity=severity,
        correlation_id=context.correlation_id,
        causation_id=context.causation_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_content_identity=subject_identity,
        payload=payload,
        previous_event_identity=context.previous_event_identity,
    )


def _valid_artifact(value: object, expected_type: type[object]) -> bool:
    if not isinstance(value, expected_type):
        return False
    try:
        if isinstance(value, AccountingEntry):
            return value == AccountingEntry.create(
                cash_delta=value.cash_delta,
                realized_pnl_delta=value.realized_pnl_delta,
                provenance=value.provenance,
            )
        if isinstance(value, AccountingReplayResult):
            return value == AccountingReplayResult.create(
                status=value.status,
                reason_code=value.reason_code,
                input_identity=value.input_identity,
                accounting_policy_identity=value.accounting_policy_identity,
                initial_cash=value.initial_cash,
                final_state=value.final_state,
                ledger=value.ledger,
            )
        if isinstance(value, AccountingValuation):
            return value == AccountingValuation.create(
                status=value.status,
                reason_code=value.reason_code,
                cash=value.cash,
                position=value.position,
                realized_pnl=value.realized_pnl,
                unrealized_pnl=value.unrealized_pnl,
                equity=value.equity,
                mark_identity=value.mark_identity,
                valuation_time=value.valuation_time,
                accounting_state_identity=value.accounting_state_identity,
                accounting_policy_identity=value.accounting_policy_identity,
            )
        value.__post_init__()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - domain adapter trust boundary must fail closed
        return False
    return True


def _valid_backtest_input(value: object) -> bool:
    if not isinstance(value, BacktestInput):
        return False
    try:
        if not _valid_artifact(value.snapshot, DatasetSnapshot) or not isinstance(
            value.steps, tuple
        ):
            return False
        return isinstance(value.content_identity, ContentIdentity)
    except Exception:  # noqa: BLE001 - causal evidence boundary must fail closed
        return False


def _backtest_occurred_at(result: BacktestResult, replay_input: BacktestInput) -> datetime | None:
    times = [
        step.strategy_evaluation.provenance.evaluation_time.value
        for step in replay_input.steps[: len(result.steps)]
    ]
    for step in result.steps:
        if step.order is not None:
            times.append(step.order.created_at)
        if step.fill is not None:
            times.append(step.fill.fill_time)
    return None if not times else max(times)


def _valid_accounting_input(value: object) -> bool:
    if not isinstance(value, AccountingReplayInput):
        return False
    try:
        if (
            not isinstance(value.initial_cash, Decimal)
            or not value.initial_cash.is_finite()
            or value.initial_cash < 0
            or value.currency != "USDT"
            or not isinstance(value.executions, tuple)
        ):
            return False
        for execution in value.executions:
            if (
                not isinstance(execution, AccountingExecution)
                or not isinstance(execution.quantity, Decimal)
                or not execution.quantity.is_finite()
                or execution.quantity <= 0
                or not _valid_artifact(execution.simulated_fill, SimulatedFill)
            ):
                return False
        return isinstance(value.content_identity, ContentIdentity)
    except Exception:  # noqa: BLE001 - causal evidence boundary must fail closed
        return False


def _accounting_replay_occurred_at(
    result: AccountingReplayResult, replay_input: AccountingReplayInput
) -> datetime | None:
    applied = len(result.ledger)
    if result.status is AccountingStatus.BLOCKED and applied < len(replay_input.executions):
        return replay_input.executions[applied].simulated_fill.fill_time
    if result.ledger:
        return result.ledger[-1].provenance.fill_time
    if replay_input.executions:
        return replay_input.executions[-1].simulated_fill.fill_time
    return None
