from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atp.backtesting import (
    BacktestInput,
    BacktestStatus,
    DeterministicBacktestEngine,
    ReplayStep,
    SimulatedOrderOutcome,
    SimulatedPositionState,
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
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    RiskEvaluationContext,
    RiskMarketContext,
    RiskPolicy,
    RiskPolicyId,
    RiskStatus,
)
from atp.shared.environment import Environment
from atp.shared.time import LogicalTime
from atp.strategy import (
    SignalKind,
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluationContext,
    StrategyId,
)


def test_data_strategy_risk_simulated_execution_vertical_slice() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = tuple(
        DataPoint.from_value(
            symbol="BTCUSDT",
            value={"close": close, "open": open_price},
            temporal=TemporalMetadata(
                event_time=start + timedelta(minutes=index),
                provider_time=start + timedelta(minutes=index),
                ingested_at=start + timedelta(minutes=index),
                available_at=start + timedelta(minutes=index),
            ),
            finality=DataFinality.FINAL,
        )
        for index, (close, open_price) in enumerate(
            (("3", "3"), ("2", "3"), ("1", "2"), ("4", "1"), ("5", "4.5"))
        )
    )
    snapshot = DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:bt-contract:v1"),
        source_id=SourceId("historical-bt-fixture"),
        environment=Environment.BACKTEST,
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        created_at=start + timedelta(minutes=4),
        points=points,
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("normalize", "v1"),)),
    )
    universe = UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe:bt-contract:v1"),
        created_at=start,
        effective_at=start,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(snapshot.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "eligible fixture", start),),
    )
    strategy = SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
    ).evaluate(
        StrategyEvaluationContext(
            environment=Environment.BACKTEST,
            snapshot=snapshot,
            universe=universe,
            evaluation_time=LogicalTime(points[3].temporal.event_time),
            symbol="BTCUSDT",
        )
    )
    assert strategy.signal is not None
    assert strategy.signal.kind is SignalKind.LONG_ENTRY
    risk = DeterministicRiskEngine(
        RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    ).evaluate(
        RiskEvaluationContext(
            strategy_evaluation=strategy,
            market_context=RiskMarketContext(
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                position_direction=PositionDirection.LONG,
                margin_enabled=False,
                leverage=Decimal(1),
                instrument_class=InstrumentClass.SPOT,
                environment=Environment.BACKTEST.value,
            ),
            portfolio_state=PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY),
        )
    )
    assert risk.status is RiskStatus.APPROVED

    result = DeterministicBacktestEngine(SimulationPolicy.v1()).replay(
        BacktestInput(
            snapshot=snapshot,
            steps=(ReplayStep(strategy, risk, points[3]),),
            initial_state=SimulatedPositionState.empty(),
        )
    )

    assert result.status is BacktestStatus.COMPLETED
    assert result.steps[0].outcome is SimulatedOrderOutcome.FILLED
    assert result.steps[0].fill is not None
    assert result.steps[0].fill.fill_price == Decimal("4.5")
    assert result.steps[0].fill.source_bar_identity == points[4].content_identity
    assert result.number_of_orders == result.number_of_fills == 1


def test_backtesting_has_no_forbidden_authority_imports() -> None:
    forbidden = ("atp.oms", "atp.exchange", "atp.accounting")
    for module_path in Path("src/atp/backtesting").glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        imported.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert all(not module.startswith(forbidden) for module in imported), module_path
