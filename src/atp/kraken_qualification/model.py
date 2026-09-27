"""Kraken-only public qualification identities and results."""

import re
from dataclasses import dataclass
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class KrakenQualificationLevel(StrEnum):
    OFFLINE_CONTRACT = "OFFLINE_CONTRACT"
    PUBLIC_CONNECTIVITY = "PUBLIC_CONNECTIVITY"


class KrakenQualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class KrakenQualificationReason(StrEnum):
    KRAKEN_SOURCE_INVALID = "KRAKEN_SOURCE_INVALID"
    KRAKEN_PUBLIC_QUALIFIED = "KRAKEN_PUBLIC_QUALIFIED"
    KRAKEN_EVIDENCE_INVALID = "KRAKEN_EVIDENCE_INVALID"
    KRAKEN_PUBLIC_UNAVAILABLE = "KRAKEN_PUBLIC_UNAVAILABLE"
    KRAKEN_SYSTEM_NOT_ONLINE = "KRAKEN_SYSTEM_NOT_ONLINE"


@dataclass(frozen=True, slots=True)
class KrakenQualificationResult(EvidenceRecord):
    level: KrakenQualificationLevel
    status: KrakenQualificationStatus
    reason_code: KrakenQualificationReason
    venue: VenueId
    instrument_identity: ContentIdentity
    mapping_identity: ContentIdentity | None
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
        expected_routes = {
            "/0/public/Time",
            "/0/public/SystemStatus",
            "/0/public/AssetPairs",
            "/0/public/OHLC",
            "/0/public/Ticker",
        }
        if (
            type(self.status) is not KrakenQualificationStatus
            or (
                self.status is KrakenQualificationStatus.PASSED
                and (
                    type(self.source_commit_sha) is not str
                    or re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is None
                    or type(self.source_tree_sha) is not str
                    or re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha) is None
                    or type(self.repository_identity) is not ContentIdentity
                    or type(self.source_identity) is not ContentIdentity
                )
            )
            or self.release_binding != "NOT_ESTABLISHED_PRE_MERGE"
            or self.venue is not VenueId.KRAKEN
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
            or set(self.route_allowlist) != expected_routes
            or any(not route.startswith("/0/public/") for route in self.route_allowlist)
        ):
            raise EvidenceError("INVALID_KRAKEN_QUALIFICATION_RESULT")
        EvidenceRecord.__post_init__(self)
