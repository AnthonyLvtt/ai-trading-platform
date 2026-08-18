from __future__ import annotations

from datetime import UTC, datetime, timedelta

from atp.data import (
    ConsumerContract,
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
    build_historical_view,
)
from atp.shared.environment import Environment


def test_historical_input_to_consumable_data_vertical_slice() -> None:
    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    available_at = event_time + timedelta(seconds=2)
    point = DataPoint.from_value(
        symbol="BTCUSDT",
        value={"close": "100.00", "volume": "2.5"},
        temporal=TemporalMetadata(
            event_time=event_time,
            provider_time=event_time,
            ingested_at=available_at,
            available_at=available_at,
        ),
        finality=DataFinality.FINAL,
    )
    snapshot = DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:2026-01:v1"),
        snapshot_id=SnapshotId("btc-usdt-1m:2026-01:materialization-1"),
        source_id=SourceId("historical-contract-fixture"),
        environment=Environment.BACKTEST,
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        created_at=available_at,
        points=(point,),
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("normalize", "v1"),)),
    )
    universe = UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe:2026-01-01:v1"),
        created_at=available_at,
        effective_at=available_at,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(snapshot.snapshot_id,),
        decisions=(
            SymbolDecision(
                symbol="BTCUSDT",
                eligible=True,
                reason="historical DATA eligibility demonstrated",
                evidence_available_at=available_at,
            ),
        ),
    )
    contract = ConsumerContract(
        accepted_quality=frozenset({DataQuality.VALID}),
        accepted_freshness=frozenset({FreshnessStatus.FRESH}),
    )

    result = build_historical_view(
        snapshot=snapshot,
        universe=universe,
        as_of=available_at,
        contract=contract,
    )

    assert result.snapshot_id == snapshot.snapshot_id
    assert result.universe_snapshot_id == universe.universe_snapshot_id
    assert result.points == (point,)
