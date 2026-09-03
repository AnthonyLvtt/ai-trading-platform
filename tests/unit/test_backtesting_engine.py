from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from atp.backtesting import (
    BacktestInput,
    BacktestReasonCode,
    BacktestStatus,
    DeterministicBacktestEngine,
    ReplayStep,
    SimulatedOrderOutcome,
    SimulatedOrderSide,
    SimulatedPositionState,
    SimulatedPositionStatus,
    SimulationPolicy,
)
from atp.data import (
    DataFinality,
    DataLineage,
    DataPoint,
    DataQuality,
    DatasetId,
    DatasetSnapshot,
    FreshnessStatus,
    GapStatus,
    LineageStep,
    SnapshotId,
    SourceId,
    SymbolDecision,
    TemporalMetadata,
    UniverseSnapshot,
    UniverseSnapshotId,
)
from atp.risk import (
    DeterministicRiskEngine,
    InstrumentClass,
    MarketType,
    OpenPosition,
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    PositionId,
    PositionSide,
    RiskEvaluationContext,
    RiskMarketContext,
    RiskPolicy,
    RiskPolicyId,
)
from atp.shared.environment import Environment
from atp.shared.time import LogicalTime
from atp.strategy import (
    SignalKind,
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluation,
    StrategyEvaluationContext,
    StrategyId,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def snapshot(
    *,
    finalities: dict[int, DataFinality] | None = None,
    payloads: dict[int, dict[str, object]] | None = None,
) -> DatasetSnapshot:
    closes = ("3", "2", "1", "4", "5", "0", "1")
    opens = ("3", "3", "2", "1", "4.5", "5.5", "0.5")
    points = tuple(
        DataPoint.from_value(
            symbol="BTCUSDT",
            value=(payloads or {}).get(index, {"close": close, "open": opens[index]}),
            temporal=TemporalMetadata(
                event_time=START + timedelta(minutes=index),
                provider_time=START + timedelta(minutes=index),
                ingested_at=START + timedelta(minutes=index),
                available_at=START + timedelta(minutes=index),
            ),
            finality=(finalities or {}).get(index, DataFinality.FINAL),
        )
        for index, close in enumerate(closes)
    )
    return DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:bt:v1"),
        source_id=SourceId("historical-fixture"),
        environment=Environment.BACKTEST,
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        created_at=START + timedelta(minutes=6),
        points=points,
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("normalize", "v1"),)),
    )


def universe(data: DatasetSnapshot) -> UniverseSnapshot:
    return UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe:bt:v1"),
        created_at=START,
        effective_at=START,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(data.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "eligible fixture", START),),
    )


def strategy_evaluation(data: DatasetSnapshot, index: int) -> StrategyEvaluation:
    return SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
    ).evaluate(
        StrategyEvaluationContext(
            environment=Environment.BACKTEST,
            snapshot=data,
            universe=universe(data),
            evaluation_time=LogicalTime(START + timedelta(minutes=index)),
            symbol="BTCUSDT",
        )
    )


def risk_result(
    evaluation: StrategyEvaluation,
    portfolio: PortfolioState,
):
    return DeterministicRiskEngine(
        RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    ).evaluate(
        RiskEvaluationContext(
            strategy_evaluation=evaluation,
            market_context=RiskMarketContext(
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                position_direction=PositionDirection.LONG,
                margin_enabled=False,
                leverage=Decimal(1),
                instrument_class=InstrumentClass.SPOT,
                environment=Environment.BACKTEST.value,
            ),
            portfolio_state=portfolio,
        )
    )


def empty_portfolio() -> PortfolioState:
    return PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY)


def open_portfolio() -> PortfolioState:
    return PortfolioState.create(
        PortfolioKnowledgeStatus.KNOWN_OPEN,
        (OpenPosition(PositionId("position:btc"), "BTCUSDT", PositionSide.LONG),),
    )


def replay_step(
    data: DatasetSnapshot,
    index: int,
    portfolio: PortfolioState,
) -> ReplayStep:
    evaluation = strategy_evaluation(data, index)
    return ReplayStep(evaluation, risk_result(evaluation, portfolio), data.points[index])


def replay(data: DatasetSnapshot, *steps: ReplayStep):
    return DeterministicBacktestEngine(SimulationPolicy.v1()).replay(
        BacktestInput(data, tuple(steps), SimulatedPositionState.empty())
    )


def test_long_entry_fills_only_at_next_bar_open_deterministically() -> None:
    data = snapshot()
    step = replay_step(data, 3, empty_portfolio())

    first = replay(data, step)
    second = replay(data, step)

    assert first == second
    assert first.backtest_run_id == second.backtest_run_id
    assert first.status is BacktestStatus.COMPLETED
    assert first.number_of_orders == first.number_of_fills == 1
    result = first.steps[0]
    assert result.outcome is SimulatedOrderOutcome.FILLED
    assert result.order is not None and result.fill is not None
    assert result.order.side is SimulatedOrderSide.BUY_ENTRY
    assert result.order.created_at == data.points[3].temporal.event_time
    assert result.fill.fill_price == Decimal("4.5")
    assert result.fill.source_bar_identity == data.points[4].content_identity
    assert result.fill.fill_time == data.points[4].temporal.available_at
    assert result.position_after == SimulatedPositionState.open_long("BTCUSDT")


def test_exit_fills_next_bar_and_returns_to_empty() -> None:
    data = snapshot()
    entry = replay_step(data, 3, empty_portfolio())
    exit_step = replay_step(data, 5, open_portfolio())

    result = replay(data, entry, exit_step)

    assert strategy_evaluation(data, 5).signal is not None
    assert strategy_evaluation(data, 5).signal.kind is SignalKind.EXIT
    assert result.number_of_orders == result.number_of_fills == 2
    assert result.steps[1].fill is not None
    assert result.steps[1].fill.fill_price == Decimal("0.5")
    assert result.steps[1].order is not None
    assert result.steps[1].order.side is SimulatedOrderSide.SELL_EXIT
    assert result.terminal_simulated_position_state == SimulatedPositionState.empty()


def test_no_action_and_non_approved_risk_never_create_orders() -> None:
    data = snapshot()
    no_action = replay_step(data, 4, open_portfolio())
    rejected_evaluation = strategy_evaluation(data, 3)
    rejected = ReplayStep(
        rejected_evaluation,
        risk_result(rejected_evaluation, open_portfolio()),
        data.points[3],
    )
    blocked_evaluation = strategy_evaluation(data, 3)
    blocked = ReplayStep(
        blocked_evaluation,
        risk_result(
            blocked_evaluation,
            PortfolioState.create(PortfolioKnowledgeStatus.UNKNOWN),
        ),
        data.points[3],
    )

    no_action_result = replay(data, no_action)
    rejected_result = replay(data, rejected)
    blocked_result = replay(data, blocked)

    assert no_action_result.steps[0].reason_code is BacktestReasonCode.STRATEGY_NO_ACTION
    assert rejected_result.steps[0].reason_code is BacktestReasonCode.RISK_REJECTED
    assert blocked_result.steps[0].reason_code is BacktestReasonCode.RISK_BLOCKED
    assert blocked_result.status is BacktestStatus.BLOCKED
    assert all(
        result.number_of_orders == result.number_of_fills == 0
        for result in (no_action_result, rejected_result, blocked_result)
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"close": "5"},
        {"close": "5", "open": "NaN"},
        {"close": "5", "open": "Infinity"},
        {"close": "5", "open": "0"},
        {"close": "5", "open": True},
    ],
)
def test_invalid_next_bar_open_blocks_without_searching_later(
    payload: dict[str, object],
) -> None:
    data = snapshot(payloads={4: payload})
    result = replay(data, replay_step(data, 3, empty_portfolio()))

    assert result.status is BacktestStatus.BLOCKED
    assert result.steps[0].outcome is SimulatedOrderOutcome.BLOCKED
    assert result.steps[0].reason_code is BacktestReasonCode.NEXT_BAR_INADMISSIBLE
    assert result.steps[0].order is not None
    assert result.steps[0].fill is None
    assert result.number_of_orders == 1
    assert result.number_of_fills == 0


def test_non_final_next_bar_blocks_without_using_later_bar() -> None:
    data = snapshot(finalities={4: DataFinality.PROVISIONAL})
    result = replay(data, replay_step(data, 3, empty_portfolio()))

    assert result.status is BacktestStatus.BLOCKED
    assert result.steps[0].reason_code is BacktestReasonCode.NEXT_BAR_INADMISSIBLE
    assert result.steps[0].fill is None


def test_inadmissible_snapshot_blocks_before_processing() -> None:
    data = snapshot()
    object.__setattr__(data, "gap_status", GapStatus.GAP_STATUS_UNKNOWN)

    result = replay(data, replay_step(snapshot(), 3, empty_portfolio()))

    assert result.status is BacktestStatus.BLOCKED
    assert result.reason_code is BacktestReasonCode.DATA_SNAPSHOT_INADMISSIBLE
    assert result.steps == ()


def test_end_of_replay_leaves_approved_order_unfilled() -> None:
    full = snapshot()
    evaluation = strategy_evaluation(full, 3)
    shortened = DatasetSnapshot.create(
        dataset_id=full.dataset_id,
        snapshot_id=full.snapshot_id,
        source_id=full.source_id,
        environment=full.environment,
        schema_version=full.schema_version,
        transformation_version=full.transformation_version,
        created_at=START + timedelta(minutes=3),
        points=full.points[:4],
        quality=full.quality,
        freshness=full.freshness,
        gap_status=full.gap_status,
        gaps=full.gaps,
        degradation_reasons=full.degradation_reasons,
        lineage=full.lineage,
    )
    evaluation = strategy_evaluation(shortened, 3)
    step = ReplayStep(evaluation, risk_result(evaluation, empty_portfolio()), shortened.points[3])

    result = replay(shortened, step)

    assert result.status is BacktestStatus.COMPLETED
    assert result.steps[0].outcome is SimulatedOrderOutcome.UNFILLED_END_OF_REPLAY
    assert result.steps[0].order is not None
    assert result.steps[0].fill is None


def test_replay_state_contradicting_risk_approval_blocks() -> None:
    data = snapshot()
    exit_evaluation = strategy_evaluation(data, 5)
    exit_approved = ReplayStep(
        exit_evaluation,
        risk_result(exit_evaluation, open_portfolio()),
        data.points[5],
    )

    result = replay(data, exit_approved)

    assert result.status is BacktestStatus.BLOCKED
    assert result.steps[0].reason_code is BacktestReasonCode.REPLAY_STATE_INCONSISTENT
    assert result.steps[0].order is None


def test_tampered_risk_linkage_blocks_without_order() -> None:
    data = snapshot()
    step = replay_step(data, 3, empty_portfolio())
    object.__setattr__(step.risk_result.provenance, "strategy_evaluation_id", None)

    result = replay(data, step)

    assert result.status is BacktestStatus.BLOCKED
    assert result.steps[0].reason_code is BacktestReasonCode.RISK_EVIDENCE_INCOMPATIBLE
    assert result.steps[0].order is None


def test_result_is_structural_without_quantity_or_financial_metrics() -> None:
    data = snapshot()
    result = replay(data, replay_step(data, 3, empty_portfolio()))
    rendered = repr(result).lower()

    assert result.number_of_risk_approved == 1
    assert result.number_of_risk_rejected == result.number_of_risk_blocked == 0
    assert result.terminal_simulated_position_state.status is SimulatedPositionStatus.OPEN_LONG
    for forbidden in ("quantity", "pnl", "sharpe", "drawdown", "fee", "slippage", "equity"):
        assert forbidden not in rendered
