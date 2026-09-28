"""Kraken private read-only qualification result."""

import re
from dataclasses import dataclass
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.private import KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class KrakenPrivateQualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class KrakenPrivateQualificationReason(StrEnum):
    KRAKEN_PRIVATE_READ_QUALIFIED = "KRAKEN_PRIVATE_READ_QUALIFIED"
    KRAKEN_PRIVATE_EVIDENCE_INVALID = "KRAKEN_PRIVATE_EVIDENCE_INVALID"
    KRAKEN_SOURCE_INVALID = "KRAKEN_SOURCE_INVALID"


@dataclass(frozen=True, slots=True)
class KrakenPrivateQualificationResult(EvidenceRecord):
    status: KrakenPrivateQualificationStatus
    reason_code: KrakenPrivateQualificationReason
    venue: VenueId
    credential_reference_identity: ContentIdentity | None
    evidence_identities: tuple[ContentIdentity, ...]
    route_allowlist: tuple[str, ...]
    source_commit_sha: str | None = None
    source_tree_sha: str | None = None
    repository_identity: ContentIdentity | None = None
    source_identity: ContentIdentity | None = None
    level: str = "OFFLINE_CONTRACT"
    private_network_calls: int = 0
    real_economic_calls: int = 0
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        source_valid = all(
            (
                type(self.source_commit_sha) is str,
                re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha or "") is not None,
                type(self.source_tree_sha) is str,
                re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha or "") is not None,
                type(self.repository_identity) is ContentIdentity,
                type(self.source_identity) is ContentIdentity,
            )
        )
        if (
            type(self.status) is not KrakenPrivateQualificationStatus
            or type(self.reason_code) is not KrakenPrivateQualificationReason
            or self.venue is not VenueId.KRAKEN
            or self.level != "OFFLINE_CONTRACT"
            or set(self.route_allowlist) != KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
            or any(not route.startswith("/0/private/") for route in self.route_allowlist)
            or self.private_network_calls != 0
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
            or (self.status is KrakenPrivateQualificationStatus.PASSED and not source_valid)
        ):
            raise EvidenceError("INVALID_KRAKEN_PRIVATE_QUALIFICATION_RESULT")
        EvidenceRecord.__post_init__(self)
