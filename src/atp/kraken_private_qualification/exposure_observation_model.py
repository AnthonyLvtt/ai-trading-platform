"""Offline-only contracts for future Kraken exposure-source observations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class OfflineExposureRoute(StrEnum):
    BALANCE_EX = "/0/private/BalanceEx"
    TRADE_VOLUME = "/0/private/TradeVolume"


class OfflineExposureValueKind(StrEnum):
    SPENDABLE_EUR = "SPENDABLE_EUR"
    TAKER_MAX_FEE_PERCENT = "TAKER_MAX_FEE_PERCENT"


class OfflineExposureStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class OfflineExposureReason(StrEnum):
    OFFLINE_EXPOSURE_CONTRACT_QUALIFIED = "OFFLINE_EXPOSURE_CONTRACT_QUALIFIED"
    SOURCE_INVALID = "SOURCE_INVALID"
    BINDING_INVALID = "BINDING_INVALID"
    OBSERVATION_SEQUENCE_INVALID = "OBSERVATION_SEQUENCE_INVALID"
    OBSERVATION_PAYLOAD_INVALID = "OBSERVATION_PAYLOAD_INVALID"


@dataclass(frozen=True, slots=True)
class OfflineExposureRequest(EvidenceRecord):
    route: OfflineExposureRoute
    credential_reference_identity: ContentIdentity
    capability_identity: ContentIdentity
    account_identity: ContentIdentity
    parameters: tuple[tuple[str, str], ...]
    scope: str

    def __post_init__(self) -> None:
        expected = {
            OfflineExposureRoute.BALANCE_EX: ((), "DEFAULT_WALLET"),
            OfflineExposureRoute.TRADE_VOLUME: (
                (("pair", "XXBTZEUR"),),
                "ACCOUNT_PAIR_BTC_EUR",
            ),
        }
        if (
            type(self.route) is not OfflineExposureRoute
            or any(
                type(identity) is not ContentIdentity
                for identity in (
                    self.credential_reference_identity,
                    self.capability_identity,
                    self.account_identity,
                )
            )
            or (self.parameters, self.scope) != expected[self.route]
        ):
            raise EvidenceError("OFFLINE_EXPOSURE_REQUEST_INVALID")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class OfflineExposureObservation:
    """Unpersisted injected response; not accepted as a real network observation."""

    route: OfflineExposureRoute
    request_identity: ContentIdentity
    payload: object
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SanitizedExposureObservation(EvidenceRecord):
    route: OfflineExposureRoute
    value_kind: OfflineExposureValueKind
    value: Decimal
    request_identity: ContentIdentity
    credential_reference_identity: ContentIdentity
    capability_identity: ContentIdentity
    account_identity: ContentIdentity
    source_identity: ContentIdentity
    observed_at: datetime
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        kind_for_route = {
            OfflineExposureRoute.BALANCE_EX: OfflineExposureValueKind.SPENDABLE_EUR,
            OfflineExposureRoute.TRADE_VOLUME: OfflineExposureValueKind.TAKER_MAX_FEE_PERCENT,
        }
        if (
            type(self.route) is not OfflineExposureRoute
            or self.value_kind is not kind_for_route[self.route]
            or type(self.value) is not Decimal
            or not self.value.is_finite()
            or self.value < 0
            or (
                self.value_kind is OfflineExposureValueKind.TAKER_MAX_FEE_PERCENT
                and self.value > 100
            )
            or any(
                type(identity) is not ContentIdentity
                for identity in (
                    self.request_identity,
                    self.credential_reference_identity,
                    self.capability_identity,
                    self.account_identity,
                    self.source_identity,
                )
            )
            or type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.side_effect_performed is not False
        ):
            raise EvidenceError("SANITIZED_EXPOSURE_OBSERVATION_INVALID")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class OfflineExposureQualificationResult(EvidenceRecord):
    status: OfflineExposureStatus
    reason_code: OfflineExposureReason
    venue: VenueId
    credential_reference_identity: ContentIdentity | None
    capability_identity: ContentIdentity | None
    account_identity: ContentIdentity | None
    request_identities: tuple[ContentIdentity, ...]
    evidence_identities: tuple[ContentIdentity, ...]
    proofs: tuple[SanitizedExposureObservation, ...]
    completed_routes: tuple[OfflineExposureRoute, ...]
    source_commit_sha: str | None
    source_tree_sha: str | None
    repository_identity: ContentIdentity | None
    source_identity: ContentIdentity | None
    offline_observations: int
    level: str = "OFFLINE_CONTRACT"
    private_network_calls: int = 0
    real_economic_calls: int = 0
    observation_qualified: bool = False
    runtime_pass_qualified: bool = False
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        source_valid = (
            type(self.source_commit_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_commit_sha) is not None
            and type(self.source_tree_sha) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.source_tree_sha) is not None
            and type(self.repository_identity) is ContentIdentity
            and type(self.source_identity) is ContentIdentity
        )
        passed = self.status is OfflineExposureStatus.PASSED
        exact_routes = (OfflineExposureRoute.BALANCE_EX, OfflineExposureRoute.TRADE_VOLUME)
        if (
            type(self.status) is not OfflineExposureStatus
            or type(self.reason_code) is not OfflineExposureReason
            or self.venue is not VenueId.KRAKEN
            or self.level != "OFFLINE_CONTRACT"
            or type(self.offline_observations) is not int
            or self.offline_observations < 0
            or self.completed_routes != exact_routes[: len(self.completed_routes)]
            or any(type(value) is not ContentIdentity for value in self.request_identities)
            or any(type(value) is not ContentIdentity for value in self.evidence_identities)
            or any(type(proof) is not SanitizedExposureObservation for proof in self.proofs)
            or self.evidence_identities != tuple(proof.content_identity for proof in self.proofs)
            or self.completed_routes != tuple(proof.route for proof in self.proofs)
            or self.private_network_calls != 0
            or self.real_economic_calls != 0
            or self.observation_qualified is not False
            or self.runtime_pass_qualified is not False
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
            or (
                passed
                and (
                    self.reason_code
                    is not OfflineExposureReason.OFFLINE_EXPOSURE_CONTRACT_QUALIFIED
                    or not source_valid
                    or self.offline_observations != 2
                    or self.completed_routes != exact_routes
                    or len(self.request_identities) != 2
                    or len(self.evidence_identities) != 2
                    or tuple(proof.request_identity for proof in self.proofs)
                    != self.request_identities
                    or any(
                        proof.credential_reference_identity != self.credential_reference_identity
                        or proof.capability_identity != self.capability_identity
                        or proof.account_identity != self.account_identity
                        for proof in self.proofs
                    )
                    or any(
                        type(value) is not ContentIdentity
                        for value in (
                            self.credential_reference_identity,
                            self.capability_identity,
                            self.account_identity,
                        )
                    )
                )
            )
            or (
                not passed
                and self.reason_code is OfflineExposureReason.OFFLINE_EXPOSURE_CONTRACT_QUALIFIED
            )
        ):
            raise EvidenceError("OFFLINE_EXPOSURE_QUALIFICATION_RESULT_INVALID")
        EvidenceRecord.__post_init__(self)
