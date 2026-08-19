from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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


def test_data_to_strategy_to_risk_decision_vertical_slice() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = tuple(
        DataPoint.from_value(
            symbol="BTCUSDT",
            value={"close": close},
            temporal=TemporalMetadata(
                event_time=start + timedelta(minutes=index),
                provider_time=start + timedelta(minutes=index),
                ingested_at=start + timedelta(minutes=index),
                available_at=start + timedelta(minutes=index),
            ),
            finality=DataFinality.FINAL,
        )
        for index, close in enumerate(("3", "2", "1", "4"))
    )
    data = DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:risk-contract:v1"),
        source_id=SourceId("historical-risk-fixture"),
        environment=Environment.BACKTEST,
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        created_at=start + timedelta(minutes=3),
        points=points,
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("normalize", "v1"),)),
    )
    universe = UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe:risk-contract:v1"),
        created_at=start,
        effective_at=start,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(data.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "eligible fixture", start),),
    )
    strategy = SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
    )
    strategy_result = strategy.evaluate(
        StrategyEvaluationContext(
            environment=Environment.BACKTEST,
            snapshot=data,
            universe=universe,
            evaluation_time=LogicalTime(start + timedelta(minutes=3)),
            symbol="BTCUSDT",
        )
    )
    assert strategy_result.signal is not None
    assert strategy_result.signal.kind is SignalKind.LONG_ENTRY
    risk_context = RiskEvaluationContext(
        strategy_evaluation=strategy_result,
        market_context=RiskMarketContext(
            market_type=MarketType.SPOT,
            position_direction=PositionDirection.LONG,
            margin_enabled=False,
            leverage=Decimal(1),
            instrument_class=InstrumentClass.SPOT,
            environment=Environment.BACKTEST.value,
        ),
        portfolio_state=PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY),
    )
    engine = DeterministicRiskEngine(
        RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    )

    first = engine.evaluate(risk_context)
    second = engine.evaluate(risk_context)

    assert first == second
    assert first.status is RiskStatus.APPROVED
    assert first.decision is not None
    assert first.provenance.strategy_evaluation_id == strategy_result.strategy_evaluation_id
    assert first.provenance.strategy_evaluation_identity == strategy_result.content_identity
    assert first.provenance.strategy_signal_identity == strategy_result.signal.content_identity
    assert first.risk_decision_id is not None


def test_strategy_and_risk_have_no_route_to_forbidden_authorities() -> None:
    forbidden = ("atp.oms", "atp.exchange", "atp.accounting")
    module_paths = [
        *Path("src/atp/strategy").glob("*.py"),
        *Path("src/atp/risk").glob("*.py"),
    ]

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        imported_modules.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert all(not imported.startswith(forbidden) for imported in imported_modules), module_path
