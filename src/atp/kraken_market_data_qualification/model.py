"""Independent qualification records for Kraken public DATA ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from atp.data.identity import SnapshotId
from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.identity import ContentIdentity

KRAKEN_MARKET_DATA_ROUTES = (
    "/0/public/AssetPairs",
    "/0/public/OHLC",
    "/0/public/SystemStatus",
    "/0/public/Time",
)


class KrakenMarketDataQualificationLevel(StrEnum):
    OFFLINE_CONTRACT = "OFFLINE_CONTRACT"
    PUBLIC_CONNECTIVITY = "PUBLIC_CONNECTIVITY"


class KrakenMarketDataQualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class KrakenMarketDataQualificationReason(StrEnum):
    KRAKEN_DATA_QUALIFIED = "KRAKEN_DATA_QUALIFIED"
    KRAKEN_DATA_EVIDENCE_INVALID = "KRAKEN_DATA_EVIDENCE_INVALID"
    KRAKEN_DATA_SOURCE_INVALID = "KRAKEN_DATA_SOURCE_INVALID"
    KRAKEN_PUBLIC_UNAVAILABLE = "KRAKEN_PUBLIC_UNAVAILABLE"
    KRAKEN_SYSTEM_NOT_ONLINE = "KRAKEN_SYSTEM_NOT_ONLINE"


@dataclass(frozen=True, slots=True)
class KrakenMarketDataQualificationResult(EvidenceRecord):
    level: KrakenMarketDataQualificationLevel
    status: KrakenMarketDataQualificationStatus
    reason_code: KrakenMarketDataQualificationReason
    venue: VenueId
    environment: Environment
    instrument_identity: ContentIdentity
    mapping_identity: ContentIdentity | None
    provenance_identity: ContentIdentity | None
    snapshot_id: SnapshotId | None
    snapshot_content_identity: ContentIdentity | None
    lineage_identity: ContentIdentity | None
    evidence_identities: tuple[ContentIdentity, ...]
    route_allowlist: tuple[str, ...]
    source_commit_sha: str | None = None
    source_tree_sha: str | None = None
    repository_identity: ContentIdentity | None = None
    source_identity: ContentIdentity | None = None
    release_binding: str = "NOT_ESTABLISHED_PRE_MERGE"
    real_economic_calls: int = 0
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        passed = self.status is KrakenMarketDataQualificationStatus.PASSED
        source_bound = (
            type(self.source_commit_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is not None
            and type(self.source_tree_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha) is not None
            and type(self.repository_identity) is ContentIdentity
            and type(self.source_identity) is ContentIdentity
        )
        integrated = (
            type(self.mapping_identity) is ContentIdentity
            and type(self.provenance_identity) is ContentIdentity
            and type(self.snapshot_id) is SnapshotId
            and type(self.snapshot_content_identity) is ContentIdentity
            and type(self.lineage_identity) is ContentIdentity
        )
        if (
            type(self.level) is not KrakenMarketDataQualificationLevel
            or type(self.status) is not KrakenMarketDataQualificationStatus
            or type(self.reason_code) is not KrakenMarketDataQualificationReason
            or self.venue is not VenueId.KRAKEN
            or self.environment not in ACTIVE_ENVIRONMENTS
            or type(self.instrument_identity) is not ContentIdentity
            or tuple(sorted(self.route_allowlist)) != KRAKEN_MARKET_DATA_ROUTES
            or any(not route.startswith("/0/public/") for route in self.route_allowlist)
            or (passed and (not source_bound or not integrated or not self.evidence_identities))
            or self.release_binding != "NOT_ESTABLISHED_PRE_MERGE"
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
        ):
            raise EvidenceError("INVALID_KRAKEN_MARKET_DATA_QUALIFICATION")
        EvidenceRecord.__post_init__(self)
