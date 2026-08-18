"""Market Data primitives with no acquisition or Exchange side effects."""

from atp.data.backfill import BackfillEvidence
from atp.data.consumption import ConsumerContract, HistoricalDataView, build_historical_view
from atp.data.identity import DatasetId, SnapshotId, SourceId, UniverseSnapshotId
from atp.data.lineage import DataLineage, LineageStep
from atp.data.snapshot import (
    DataFinality,
    DataPoint,
    DataQuality,
    DatasetSnapshot,
    FreshnessStatus,
    Gap,
    GapStatus,
    assert_snapshot_consistent,
)
from atp.data.temporal import AvailabilityRule, TemporalMetadata
from atp.data.universe import SymbolDecision, UniverseSnapshot

__all__ = [
    "AvailabilityRule",
    "BackfillEvidence",
    "ConsumerContract",
    "DataFinality",
    "DataLineage",
    "DataPoint",
    "DataQuality",
    "DatasetId",
    "DatasetSnapshot",
    "FreshnessStatus",
    "Gap",
    "GapStatus",
    "HistoricalDataView",
    "LineageStep",
    "SnapshotId",
    "SourceId",
    "SymbolDecision",
    "TemporalMetadata",
    "UniverseSnapshot",
    "UniverseSnapshotId",
    "assert_snapshot_consistent",
    "build_historical_view",
]
