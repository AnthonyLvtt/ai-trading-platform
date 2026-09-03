from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import cast

from atp.backtesting.model import (
    BacktestReasonCode,
    BacktestResult,
    BacktestStatus,
    ExecutionProvenance,
    ReplayStepResult,
    SimulatedFill,
    SimulatedOrder,
    SimulatedOrderOutcome,
    SimulatedOrderSide,
    SimulatedPositionState,
    SimulatedPositionStatus,
)
from atp.backtesting.policy import SimulationPolicy
from atp.data.snapshot import (
    DataFinality,
    DataPoint,
    DataQuality,
    DatasetSnapshot,
    FreshnessStatus,
    GapStatus,
)
from atp.risk.model import RiskProcessingResult, RiskStatus
from atp.shared.identity import ContentIdentity
from atp.strategy.model import EvaluationStatus, SignalKind, StrategyEvaluation


@dataclass(frozen=True, slots=True)
class ReplayStep:
    strategy_evaluation: StrategyEvaluation
    risk_result: RiskProcessingResult
    evaluation_bar: DataPoint

    def canonical_value(self) -> dict[str, str]:
        return {
            "evaluation_bar_identity": str(self.evaluation_bar.content_identity),
            "risk_result_identity": str(self.risk_result.content_identity),
            "strategy_evaluation_identity": str(self.strategy_evaluation.content_identity),
        }


@dataclass(frozen=True, slots=True)
class BacktestInput:
    snapshot: DatasetSnapshot
    steps: tuple[ReplayStep, ...]
    initial_state: SimulatedPositionState

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(
            {
                "initial_state": self.initial_state.canonical_value(),
                "snapshot_identity": str(self.snapshot.content_identity),
                "steps": [step.canonical_value() for step in self.steps],
            }
        )


@dataclass(frozen=True, slots=True)
class DeterministicBacktestEngine:
    policy: SimulationPolicy

    def replay(self, replay_input: BacktestInput) -> BacktestResult:
        if not _snapshot_admissible(replay_input.snapshot):
            return _blocked_input_result(
                replay_input,
                self.policy,
                BacktestReasonCode.DATA_SNAPSHOT_INADMISSIBLE,
            )
        if not _steps_strictly_ordered(replay_input.steps):
            return _blocked_input_result(
                replay_input,
                self.policy,
                BacktestReasonCode.EVALUATION_BAR_INADMISSIBLE,
            )

        state = replay_input.initial_state
        results: list[ReplayStepResult] = []
        for step in replay_input.steps:
            result = self._process_step(replay_input.snapshot, step, state)
            results.append(result)
            state = result.position_after
            if result.outcome is SimulatedOrderOutcome.BLOCKED or (
                result.reason_code is BacktestReasonCode.RISK_BLOCKED
            ):
                break

        status = (
            BacktestStatus.BLOCKED
            if any(
                result.outcome is SimulatedOrderOutcome.BLOCKED
                or result.reason_code is BacktestReasonCode.RISK_BLOCKED
                for result in results
            )
            else BacktestStatus.COMPLETED
        )
        return BacktestResult.create(
            status=status,
            reason_code=None if status is BacktestStatus.COMPLETED else results[-1].reason_code,
            input_identity=replay_input.content_identity,
            simulation_policy_identity=self.policy.content_identity,
            steps=tuple(results),
            terminal_state=state,
        )

    def _process_step(
        self,
        snapshot: DatasetSnapshot,
        step: ReplayStep,
        state: SimulatedPositionState,
    ) -> ReplayStepResult:
        if not _step_evidence_compatible(snapshot, step):
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.BLOCKED,
                BacktestReasonCode.RISK_EVIDENCE_INCOMPATIBLE,
            )

        risk = step.risk_result
        if risk.status is RiskStatus.NO_DECISION:
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.NO_ORDER,
                BacktestReasonCode.STRATEGY_NO_ACTION,
            )
        if risk.status is RiskStatus.REJECTED:
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.NO_ORDER,
                BacktestReasonCode.RISK_REJECTED,
            )
        if risk.status is RiskStatus.BLOCKED:
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.NO_ORDER,
                BacktestReasonCode.RISK_BLOCKED,
            )

        signal = step.strategy_evaluation.signal
        assert signal is not None
        expected_state = (
            state.status is SimulatedPositionStatus.EMPTY
            if signal.kind is SignalKind.LONG_ENTRY
            else state.status is SimulatedPositionStatus.OPEN_LONG
            and state.symbol == step.evaluation_bar.symbol
        )
        if not expected_state:
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.BLOCKED,
                BacktestReasonCode.REPLAY_STATE_INCONSISTENT,
            )

        order = _create_order(step, snapshot, self.policy)
        next_bar = _next_symbol_bar(snapshot, step.evaluation_bar)
        if next_bar is None:
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.UNFILLED_END_OF_REPLAY,
                BacktestReasonCode.END_OF_REPLAY,
                order=order,
            )
        if not _next_bar_admissible(snapshot, order, step.evaluation_bar, next_bar):
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.BLOCKED,
                BacktestReasonCode.NEXT_BAR_INADMISSIBLE,
                order=order,
            )
        try:
            fill_price = _open_price(next_bar)
        except ValueError:
            return _step_result(
                step,
                state,
                SimulatedOrderOutcome.BLOCKED,
                BacktestReasonCode.NEXT_BAR_INADMISSIBLE,
                order=order,
            )
        fill = SimulatedFill.create(
            order=order,
            fill_price=fill_price,
            fill_time=next_bar.temporal.available_at,
            source_bar_identity=next_bar.content_identity,
            source_bar_event_time=next_bar.temporal.event_time,
        )
        after = (
            SimulatedPositionState.open_long(order.symbol)
            if order.side is SimulatedOrderSide.BUY_ENTRY
            else SimulatedPositionState.empty()
        )
        return _step_result(
            step,
            state,
            SimulatedOrderOutcome.FILLED,
            BacktestReasonCode.STEP_EXECUTED,
            order=order,
            fill=fill,
            position_after=after,
        )


def _snapshot_admissible(snapshot: DatasetSnapshot) -> bool:
    if (
        snapshot.quality is not DataQuality.VALID
        or snapshot.validation_as_of_use is not DataQuality.VALID
        or snapshot.freshness is not FreshnessStatus.FRESH
        or snapshot.gap_status is not GapStatus.NO_GAP_DETECTED
        or snapshot.gaps
    ):
        return False
    try:
        replace(snapshot.lineage)
        replace(snapshot)
    except Exception:
        return False
    symbols = {point.symbol for point in snapshot.points}
    return all(
        all(
            previous.temporal.event_time < current.temporal.event_time
            for previous, current in pairwise(
                point for point in snapshot.points if point.symbol == symbol
            )
        )
        for symbol in symbols
    )


def _steps_strictly_ordered(steps: tuple[ReplayStep, ...]) -> bool:
    try:
        times = tuple(step.evaluation_bar.temporal.event_time for step in steps)
    except AttributeError:
        return False
    return all(previous < current for previous, current in pairwise(times))


def _step_evidence_compatible(snapshot: DatasetSnapshot, step: ReplayStep) -> bool:
    evaluation = step.strategy_evaluation
    risk = step.risk_result
    bar = step.evaluation_bar
    if (
        not isinstance(evaluation, StrategyEvaluation)
        or evaluation.status is not EvaluationStatus.COMPLETED
        or evaluation.signal is None
        or not isinstance(risk, RiskProcessingResult)
        or not isinstance(bar, DataPoint)
        or bar.finality is not DataFinality.FINAL
        or bar.symbol != evaluation.provenance.symbol
        or evaluation.provenance.snapshot_id != snapshot.snapshot_id
        or evaluation.provenance.snapshot_content_identity != snapshot.content_identity
        or evaluation.provenance.evaluation_time.value != bar.temporal.event_time
        or bar.temporal.available_at > evaluation.provenance.evaluation_time.value
        or not evaluation.provenance.used_data
        or evaluation.provenance.used_data[-1].content_identity != bar.content_identity
        or evaluation.provenance.used_data[-1].event_time != bar.temporal.event_time
        or risk.provenance.strategy_evaluation_id != evaluation.strategy_evaluation_id
        or risk.provenance.strategy_evaluation_identity != evaluation.content_identity
        or risk.provenance.strategy_signal_identity != evaluation.signal.content_identity
        or risk.provenance.strategy_decision_id != evaluation.signal.strategy_decision_id
    ):
        return False
    try:
        replace(evaluation.provenance.configuration)
        replace(evaluation.provenance)
        replace(evaluation.signal)
        replace(evaluation)
        replace(risk.provenance)
        if risk.decision is not None:
            replace(risk.decision)
        replace(risk)
        replace(bar.temporal)
        replace(bar)
    except Exception:
        return False
    if risk.status is RiskStatus.APPROVED:
        return (
            risk.decision is not None
            and risk.decision.status is RiskStatus.APPROVED
            and risk.risk_decision_id is not None
            and evaluation.signal.kind in {SignalKind.LONG_ENTRY, SignalKind.EXIT}
        )
    return risk.status in {RiskStatus.NO_DECISION, RiskStatus.REJECTED, RiskStatus.BLOCKED}


def _create_order(
    step: ReplayStep,
    snapshot: DatasetSnapshot,
    policy: SimulationPolicy,
) -> SimulatedOrder:
    evaluation = step.strategy_evaluation
    signal = evaluation.signal
    decision = step.risk_result.decision
    assert signal is not None
    assert signal.strategy_decision_id is not None
    assert decision is not None
    side = (
        SimulatedOrderSide.BUY_ENTRY
        if signal.kind is SignalKind.LONG_ENTRY
        else SimulatedOrderSide.SELL_EXIT
    )
    provenance = ExecutionProvenance(
        dataset_id=snapshot.dataset_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_identity=snapshot.content_identity,
        evaluation_bar_identity=step.evaluation_bar.content_identity,
        evaluation_bar_event_time=step.evaluation_bar.temporal.event_time,
        evaluation_time=evaluation.provenance.evaluation_time.value,
        strategy_evaluation_id=evaluation.strategy_evaluation_id,
        strategy_evaluation_identity=evaluation.content_identity,
        strategy_decision_id=signal.strategy_decision_id,
        strategy_signal_identity=signal.content_identity,
        risk_decision_id=decision.risk_decision_id,
        risk_decision_identity=decision.content_identity,
        risk_status=decision.status,
        risk_reason_code=decision.reason_code,
        simulation_policy_id=policy.policy_id,
        simulation_policy_version=policy.version,
        simulation_policy_identity=policy.content_identity,
        symbol=step.evaluation_bar.symbol,
    )
    return SimulatedOrder.create(
        side=side,
        created_at=evaluation.provenance.evaluation_time.value,
        provenance=provenance,
    )


def _next_symbol_bar(snapshot: DatasetSnapshot, evaluation_bar: DataPoint) -> DataPoint | None:
    for point in snapshot.points:
        if (
            point.symbol == evaluation_bar.symbol
            and point.temporal.event_time > evaluation_bar.temporal.event_time
        ):
            return point
    return None


def _next_bar_admissible(
    snapshot: DatasetSnapshot,
    order: SimulatedOrder,
    evaluation_bar: DataPoint,
    next_bar: DataPoint,
) -> bool:
    del snapshot
    return (
        next_bar.symbol == order.symbol == evaluation_bar.symbol
        and next_bar.finality is DataFinality.FINAL
        and next_bar.temporal.event_time > evaluation_bar.temporal.event_time
        and order.created_at <= evaluation_bar.temporal.event_time
        and next_bar.temporal.available_at >= next_bar.temporal.event_time
    )


def _open_price(point: DataPoint) -> Decimal:
    payload = cast(object, json.loads(point.canonical_payload))
    if not isinstance(payload, dict) or "open" not in payload:
        raise ValueError("candle open is required")
    raw = payload["open"]
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise ValueError("candle open must be numeric")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("candle open must be numeric") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("candle open must be finite and positive")
    return value


def _step_result(
    step: ReplayStep,
    before: SimulatedPositionState,
    outcome: SimulatedOrderOutcome,
    reason_code: BacktestReasonCode,
    *,
    order: SimulatedOrder | None = None,
    fill: SimulatedFill | None = None,
    position_after: SimulatedPositionState | None = None,
) -> ReplayStepResult:
    return ReplayStepResult(
        outcome=outcome,
        reason_code=reason_code,
        risk_status=step.risk_result.status,
        risk_reason_code=step.risk_result.reason_code,
        order=order,
        fill=fill,
        position_before=before,
        position_after=before if position_after is None else position_after,
    )


def _blocked_input_result(
    replay_input: BacktestInput,
    policy: SimulationPolicy,
    reason_code: BacktestReasonCode,
) -> BacktestResult:
    return BacktestResult.create(
        status=BacktestStatus.BLOCKED,
        reason_code=reason_code,
        input_identity=replay_input.content_identity,
        simulation_policy_identity=policy.content_identity,
        steps=(),
        terminal_state=replay_input.initial_state,
    )
