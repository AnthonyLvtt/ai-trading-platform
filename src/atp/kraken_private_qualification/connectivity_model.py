"""Sanitized result for explicit Kraken private connectivity qualification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.private import (
    KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST,
    KrakenPrivateReadRoute,
)
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class KrakenPrivateConnectivityStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class KrakenPrivateConnectivityReason(StrEnum):
    KRAKEN_PRIVATE_CONNECTIVITY_QUALIFIED = "KRAKEN_PRIVATE_CONNECTIVITY_QUALIFIED"
    KRAKEN_SOURCE_INVALID = "KRAKEN_SOURCE_INVALID"
    KRAKEN_CREDENTIAL_UNAVAILABLE = "KRAKEN_CREDENTIAL_UNAVAILABLE"
    KRAKEN_CREDENTIAL_INVALID = "KRAKEN_CREDENTIAL_INVALID"
    KRAKEN_CREDENTIAL_BINDING_MISMATCH = "KRAKEN_CREDENTIAL_BINDING_MISMATCH"
    KRAKEN_LEAST_PRIVILEGE_REQUIRED = "KRAKEN_LEAST_PRIVILEGE_REQUIRED"
    KRAKEN_AUTH_REJECTED = "KRAKEN_AUTH_REJECTED"
    KRAKEN_NONCE_REJECTED = "KRAKEN_NONCE_REJECTED"
    KRAKEN_NONCE_OR_SIGNING_FAILURE = "KRAKEN_NONCE_OR_SIGNING_FAILURE"
    KRAKEN_PERMISSION_REJECTED = "KRAKEN_PERMISSION_REJECTED"
    KRAKEN_PRIVATE_TIMEOUT = "KRAKEN_PRIVATE_TIMEOUT"
    KRAKEN_PRIVATE_NETWORK_FAILURE = "KRAKEN_PRIVATE_NETWORK_FAILURE"
    KRAKEN_PRIVATE_HTTP_FAILURE = "KRAKEN_PRIVATE_HTTP_FAILURE"
    KRAKEN_PRIVATE_RESPONSE_TOO_LARGE = "KRAKEN_PRIVATE_RESPONSE_TOO_LARGE"
    KRAKEN_PRIVATE_RESPONSE_INVALID = "KRAKEN_PRIVATE_RESPONSE_INVALID"
    KRAKEN_PRIVATE_JSON_INVALID = "KRAKEN_PRIVATE_JSON_INVALID"
    KRAKEN_PRIVATE_API_REJECTED = "KRAKEN_PRIVATE_API_REJECTED"
    KRAKEN_PRIVATE_EVIDENCE_INVALID = "KRAKEN_PRIVATE_EVIDENCE_INVALID"


@dataclass(frozen=True, slots=True)
class KrakenPrivateConnectivityResult(EvidenceRecord):
    status: KrakenPrivateConnectivityStatus
    reason_code: KrakenPrivateConnectivityReason
    venue: VenueId
    credential_reference_identity: ContentIdentity | None
    evidence_identities: tuple[ContentIdentity, ...]
    completed_routes: tuple[str, ...]
    route_allowlist: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    source_commit_sha: str | None = None
    source_tree_sha: str | None = None
    repository_identity: ContentIdentity | None = None
    source_identity: ContentIdentity | None = None
    level: str = "PRIVATE_CONNECTIVITY"
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
        passed = self.status is KrakenPrivateConnectivityStatus.PASSED
        expected_routes = tuple(route.value for route in _route_order())
        if (
            type(self.status) is not KrakenPrivateConnectivityStatus
            or type(self.reason_code) is not KrakenPrivateConnectivityReason
            or self.venue is not VenueId.KRAKEN
            or self.level != "PRIVATE_CONNECTIVITY"
            or set(self.route_allowlist) != KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
            or self.started_at.tzinfo is None
            or self.completed_at.tzinfo is None
            or self.completed_at < self.started_at
            or not 0 <= self.private_network_calls <= 3
            or len(self.completed_routes) > self.private_network_calls
            or self.completed_routes != expected_routes[: len(self.completed_routes)]
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
            or (
                passed
                and (
                    self.reason_code
                    is not KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_CONNECTIVITY_QUALIFIED
                    or self.private_network_calls != 3
                    or self.completed_routes != expected_routes
                    or len(self.evidence_identities) != 4
                    or type(self.credential_reference_identity) is not ContentIdentity
                    or not source_valid
                )
            )
            or (
                not passed
                and self.reason_code
                is KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_CONNECTIVITY_QUALIFIED
            )
        ):
            raise EvidenceError("INVALID_KRAKEN_PRIVATE_CONNECTIVITY_RESULT")
        EvidenceRecord.__post_init__(self)


def _route_order() -> tuple[KrakenPrivateReadRoute, ...]:
    return (
        KrakenPrivateReadRoute.API_KEY_INFO,
        KrakenPrivateReadRoute.BALANCE,
        KrakenPrivateReadRoute.OPEN_ORDERS,
    )
