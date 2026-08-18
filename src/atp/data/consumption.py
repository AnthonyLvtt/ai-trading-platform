from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp.data.identity import SnapshotId, UniverseSnapshotId
from atp.data.snapshot import DataPoint, DataQuality, DatasetSnapshot, FreshnessStatus
from atp.data.universe import UniverseSnapshot
from atp.shared.errors import DomainError
from atp.shared.time import require_utc


@dataclass(frozen=True, slots=True)
class ConsumerContract:
    accepted_quality: frozenset[DataQuality]
    accepted_freshness: frozenset[FreshnessStatus]
    allowed_degradations: frozenset[str] = frozenset()

    def accepts(self, snapshot: DatasetSnapshot) -> bool:
        if snapshot.current_validation_status not in self.accepted_quality:
            return False
        if snapshot.freshness not in self.accepted_freshness:
            return False
        if snapshot.current_validation_status is DataQuality.DEGRADED:
            return bool(snapshot.degradation_reasons) and (
                snapshot.degradation_reasons <= self.allowed_degradations
            )
        return True


@dataclass(frozen=True, slots=True)
class HistoricalDataView:
    as_of: datetime
    snapshot_id: SnapshotId
    universe_snapshot_id: UniverseSnapshotId
    points: tuple[DataPoint, ...]

    def __post_init__(self) -> None:
        require_utc(self.as_of)


def build_historical_view(
    *,
    snapshot: DatasetSnapshot,
    universe: UniverseSnapshot,
    as_of: datetime,
    contract: ConsumerContract,
) -> HistoricalDataView:
    require_utc(as_of)
    if not contract.accepts(snapshot):
        raise DomainError("snapshot is not compatible with the consumer contract")
    if universe.effective_at > as_of:
        raise DomainError("future universe snapshot is not available at requested time")
    if snapshot.snapshot_id not in universe.source_snapshot_ids:
        raise DomainError("universe is not linked to the requested data snapshot")
    visible = tuple(
        point
        for point in snapshot.points
        if point.symbol in universe.eligible_symbols and point.temporal.is_available_at(as_of)
    )
    return HistoricalDataView(
        as_of=as_of,
        snapshot_id=snapshot.snapshot_id,
        universe_snapshot_id=universe.universe_snapshot_id,
        points=visible,
    )
