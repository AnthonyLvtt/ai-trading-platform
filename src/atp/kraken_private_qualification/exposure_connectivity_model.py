"""Sanitized, source-bound result for two bounded exposure private reads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.kraken_private_qualification.exposure_observation_model import (
    OfflineExposureRoute,
    SanitizedExposureObservation,
)
from atp.shared.identity import ContentIdentity


class ExposureConnectivityStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class ExposureConnectivityReason(StrEnum):
    QUALIFIED = "EXPOSURE_PRIVATE_OBSERVATIONS_QUALIFIED"
    SOURCE_INVALID = "EXPOSURE_SOURCE_INVALID"
    CREDENTIAL_INVALID = "EXPOSURE_CREDENTIAL_INVALID"
    BINDING_INVALID = "EXPOSURE_BINDING_INVALID"
    NETWORK_FAILURE = "EXPOSURE_NETWORK_FAILURE"
    RESPONSE_INVALID = "EXPOSURE_RESPONSE_INVALID"
    API_REJECTED = "EXPOSURE_API_REJECTED"
    EVIDENCE_INVALID = "EXPOSURE_EVIDENCE_INVALID"
    BALANCE_ASSET_UNSUPPORTED = "EXPOSURE_BALANCE_ASSET_UNSUPPORTED"
    BALANCE_FIELDS_INCOMPLETE = "EXPOSURE_BALANCE_FIELDS_INCOMPLETE"
    BALANCE_EUR_MISSING = "EXPOSURE_BALANCE_EUR_MISSING"


@dataclass(frozen=True, slots=True)
class ExposureConnectivityResult(EvidenceRecord):
    status: ExposureConnectivityStatus
    reason_code: ExposureConnectivityReason
    venue: VenueId
    credential_reference_identity: ContentIdentity | None
    capability_identity: ContentIdentity | None
    account_identity: ContentIdentity | None
    request_identities: tuple[ContentIdentity, ...]
    proofs: tuple[SanitizedExposureObservation, ...]
    completed_routes: tuple[OfflineExposureRoute, ...]
    started_at: datetime
    completed_at: datetime
    private_network_calls: int
    source_commit_sha: str | None
    source_tree_sha: str | None
    repository_identity: ContentIdentity | None
    source_identity: ContentIdentity | None
    level: str = "EXPOSURE_PRIVATE_OBSERVATION"
    real_economic_calls: int = 0
    runtime_pass_qualified: bool = False
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        routes = (OfflineExposureRoute.BALANCE_EX, OfflineExposureRoute.TRADE_VOLUME)
        passed = self.status is ExposureConnectivityStatus.PASSED
        source_valid = (
            type(self.source_commit_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is not None
            and type(self.source_tree_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha) is not None
            and type(self.repository_identity) is ContentIdentity
            and type(self.source_identity) is ContentIdentity
        )
        if (
            type(self.status) is not ExposureConnectivityStatus
            or type(self.reason_code) is not ExposureConnectivityReason
            or self.venue is not VenueId.KRAKEN
            or self.level != "EXPOSURE_PRIVATE_OBSERVATION"
            or type(self.started_at) is not datetime
            or self.started_at.tzinfo is None
            or type(self.completed_at) is not datetime
            or self.completed_at.tzinfo is None
            or self.completed_at < self.started_at
            or type(self.private_network_calls) is not int
            or not 0 <= self.private_network_calls <= 2
            or self.completed_routes != routes[: len(self.completed_routes)]
            or len(self.completed_routes) > self.private_network_calls
            or len(self.request_identities) > 2
            or any(type(value) is not ContentIdentity for value in self.request_identities)
            or len(self.proofs) > len(self.completed_routes)
            or tuple(proof.route for proof in self.proofs)
            != self.completed_routes[: len(self.proofs)]
            or self.real_economic_calls != 0
            or self.runtime_pass_qualified is not False
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
            or (
                passed
                and (
                    self.reason_code is not ExposureConnectivityReason.QUALIFIED
                    or self.private_network_calls != 2
                    or self.completed_routes != routes
                    or len(self.request_identities) != 2
                    or len(self.proofs) != 2
                    or not source_valid
                    or any(
                        type(value) is not ContentIdentity
                        for value in (
                            self.credential_reference_identity,
                            self.capability_identity,
                            self.account_identity,
                        )
                    )
                    or any(
                        proof.request_identity != request_identity
                        or proof.credential_reference_identity != self.credential_reference_identity
                        or proof.capability_identity != self.capability_identity
                        or proof.account_identity != self.account_identity
                        for proof, request_identity in zip(
                            self.proofs, self.request_identities, strict=True
                        )
                    )
                )
            )
            or (not passed and self.reason_code is ExposureConnectivityReason.QUALIFIED)
        ):
            raise EvidenceError("EXPOSURE_CONNECTIVITY_RESULT_INVALID")
        EvidenceRecord.__post_init__(self)
