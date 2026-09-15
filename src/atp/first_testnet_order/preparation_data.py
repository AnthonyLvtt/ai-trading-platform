"""BTCUSDT final five-minute candles into the existing DATA contract."""

from datetime import datetime, timedelta

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
from atp.exchange.read_only import EvidenceError, decimal_field, safe_json, timestamp
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity


def candle_snapshot(raw: object, at: datetime) -> tuple[DatasetSnapshot, UniverseSnapshot]:
    safe_json(raw)
    if type(raw) is not list or len(raw) != 51:
        raise EvidenceError("FIRST_ORDER_NOT_READY")
    points = []
    previous = None
    for row in raw:
        if (
            type(row) is not list
            or len(row) < 7
            or type(row[0]) is not int
            or type(row[6]) is not int
        ):
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        if row[0] % 300000 or row[6] != row[0] + 299999:
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        end = timestamp(row[6]) + timedelta(milliseconds=1)
        if end > at or (previous is not None and end - previous != timedelta(minutes=5)):
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        prices = [decimal_field(row[i]) for i in (1, 2, 3, 4)]
        opening, high, low, close = prices
        if min(prices) <= 0 or not low <= min(opening, close) <= max(opening, close) <= high:
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        decimal_field(row[5])
        points.append(
            DataPoint.from_value(
                symbol="BTCUSDT",
                value={"close": str(close), "interval": "5m"},
                temporal=TemporalMetadata(end, end, at, at),
                finality=DataFinality.FINAL,
            )
        )
        previous = end
    if previous is None or not timedelta(0) <= at - previous < timedelta(minutes=5):
        raise EvidenceError("FIRST_ORDER_NOT_READY")
    identity = ContentIdentity.from_canonical(raw)
    snapshot = DatasetSnapshot.create(
        dataset_id=DatasetId("binance-testnet-BTCUSDT-5m"),
        snapshot_id=SnapshotId(str(identity)),
        source_id=SourceId("binance-spot-testnet"),
        environment=Environment.TESTNET,
        schema_version="1.0",
        transformation_version="ENG-TO-OPS-002/1.0",
        created_at=at,
        points=tuple(points),
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("testnet-final-klines", "1.0", (identity,)),)),
    )
    universe = UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId(str(identity)),
        created_at=at,
        effective_at=at,
        rules_version="ENG-TO-OPS-002/1.0",
        source_snapshot_ids=(snapshot.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "CTO_SCOPED_SYMBOL", at),),
    )
    return snapshot, universe
