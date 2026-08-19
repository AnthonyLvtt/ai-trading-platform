from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from atp.shared.environment import Environment
from atp.shared.time import LogicalTime
from atp.strategy import (
    EvaluationStatus,
    SignalKind,
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluationContext,
    StrategyId,
)


def test_validated_data_to_deterministic_strategy_signal_vertical_slice() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = ("3", "2", "1", "4")
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
        for index, close in enumerate(closes)
    )
    data = DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:strategy-contract:v1"),
        source_id=SourceId("historical-contract-fixture"),
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
    market_universe = UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe:strategy-contract:v1"),
        created_at=start,
        effective_at=start,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(data.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "eligible fixture", start),),
    )
    evaluator = SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
    )
    context = StrategyEvaluationContext(
        environment=Environment.BACKTEST,
        snapshot=data,
        universe=market_universe,
        evaluation_time=LogicalTime(start + timedelta(minutes=3)),
        symbol="BTCUSDT",
    )

    first = evaluator.evaluate(context)
    second = evaluator.evaluate(context)

    assert first == second
    assert first.status is EvaluationStatus.COMPLETED
    assert first.signal is not None
    assert first.signal.kind is SignalKind.LONG_ENTRY
    assert first.signal.strategy_decision_id is not None
    assert first.provenance.snapshot_id == data.snapshot_id
    assert first.provenance.schema_version == data.schema_version
    assert first.provenance.transformation_version == data.transformation_version
    assert first.provenance.lineage_content_identity == data.lineage.content_identity
    assert first.provenance.universe_snapshot_id == market_universe.universe_snapshot_id
