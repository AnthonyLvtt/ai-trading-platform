"""Source-bound offline preparation for two future Kraken private reads.

No credential loader, signer, transport, or real private-call route is used.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.extended_balance import (
    ExtendedBalanceShapeError,
    parse_offline_extended_balance,
)
from atp.exchange.kraken.trade_volume import (
    TradeVolumeShapeError,
    parse_offline_trade_volume_fee_bound,
)
from atp.exchange.private_contracts import (
    PrivateCredentialCapabilityEvidence,
    PrivateCredentialReference,
)
from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_private_qualification.exposure_observation_model import (
    OfflineExposureObservation,
    OfflineExposureQualificationResult,
    OfflineExposureReason,
    OfflineExposureRequest,
    OfflineExposureRoute,
    OfflineExposureStatus,
    OfflineExposureValueKind,
    SanitizedExposureObservation,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity


class _OfflineFailure(ValueError):
    def __init__(self, reason: OfflineExposureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def exact_offline_exposure_requests(
    reference: PrivateCredentialReference,
    capability: PrivateCredentialCapabilityEvidence,
    account_identity: ContentIdentity,
) -> tuple[OfflineExposureRequest, OfflineExposureRequest]:
    """Describe exact requests without making them signable or transportable."""
    if (
        not verify_record(reference, PrivateCredentialReference)
        or not verify_record(capability, PrivateCredentialCapabilityEvidence)
        or type(account_identity) is not ContentIdentity
        or capability.credential_reference_identity != reference.content_identity
        or reference.venue is not VenueId.KRAKEN
        or capability.venue is not VenueId.KRAKEN
        or capability.funds_query is not True
        or capability.trading_capability_absent is not True
        or capability.withdrawal_capability_absent is not True
    ):
        raise EvidenceError("OFFLINE_EXPOSURE_BINDING_INVALID")
    return (
        OfflineExposureRequest(
            route=OfflineExposureRoute.BALANCE_EX,
            credential_reference_identity=reference.content_identity,
            capability_identity=capability.content_identity,
            account_identity=account_identity,
            parameters=(),
            scope="DEFAULT_WALLET",
        ),
        OfflineExposureRequest(
            route=OfflineExposureRoute.TRADE_VOLUME,
            credential_reference_identity=reference.content_identity,
            capability_identity=capability.content_identity,
            account_identity=account_identity,
            parameters=(("pair", "XXBTZEUR"),),
            scope="ACCOUNT_PAIR_BTC_EUR",
        ),
    )


def _source_before(root: Path, expected_sha: str) -> SourceTree:
    if type(expected_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
        raise _OfflineFailure(OfflineExposureReason.SOURCE_INVALID)
    try:
        source = inspect_source(root)
    except (ReleaseError, OSError):
        raise _OfflineFailure(OfflineExposureReason.SOURCE_INVALID) from None
    if (
        not source.clean
        or source.source_branch != "main"
        or source.source_commit_sha != expected_sha
    ):
        raise _OfflineFailure(OfflineExposureReason.SOURCE_INVALID)
    return source


def _proof(
    observation: OfflineExposureObservation,
    request: OfflineExposureRequest,
) -> SanitizedExposureObservation:
    try:
        if request.route is OfflineExposureRoute.BALANCE_EX:
            rows = parse_offline_extended_balance(observation.payload)
            eur = tuple(row for row in rows if row.asset == "EUR")
            if len(eur) != 1:
                raise _OfflineFailure(OfflineExposureReason.BALANCE_EUR_MISSING)
            kind = OfflineExposureValueKind.SPENDABLE_EUR
            value = eur[0].available
        else:
            bound = parse_offline_trade_volume_fee_bound(observation.payload)
            kind = OfflineExposureValueKind.TAKER_MAX_FEE_PERCENT
            value = bound.taker_max_fee_percent
        return SanitizedExposureObservation(
            route=request.route,
            value_kind=kind,
            value=value,
            request_identity=request.content_identity,
            credential_reference_identity=request.credential_reference_identity,
            capability_identity=request.capability_identity,
            account_identity=request.account_identity,
            source_identity=ContentIdentity.from_canonical(observation.payload),
            observed_at=observation.observed_at,
        )
    except ExtendedBalanceShapeError as exc:
        reason = OfflineExposureReason.OBSERVATION_PAYLOAD_INVALID
        if str(exc) == "BALANCE_EX_ASSET_UNSUPPORTED":
            reason = OfflineExposureReason.BALANCE_ASSET_UNSUPPORTED
        elif str(exc) == "BALANCE_EX_FIELDS_INCOMPLETE":
            reason = OfflineExposureReason.BALANCE_FIELDS_INCOMPLETE
        raise _OfflineFailure(reason) from None
    except (TradeVolumeShapeError, EvidenceError, TypeError, ValueError):
        raise _OfflineFailure(OfflineExposureReason.OBSERVATION_PAYLOAD_INVALID) from None


def _result(
    *,
    status: OfflineExposureStatus,
    reason: OfflineExposureReason,
    source: SourceTree | None,
    reference: PrivateCredentialReference | None,
    capability: PrivateCredentialCapabilityEvidence | None,
    account_identity: ContentIdentity | None,
    requests: tuple[OfflineExposureRequest, ...],
    proofs: tuple[SanitizedExposureObservation, ...],
    offline_observations: int,
) -> OfflineExposureQualificationResult:
    return OfflineExposureQualificationResult(
        status=status,
        reason_code=reason,
        venue=VenueId.KRAKEN,
        credential_reference_identity=None if reference is None else reference.content_identity,
        capability_identity=None if capability is None else capability.content_identity,
        account_identity=account_identity,
        request_identities=tuple(request.content_identity for request in requests),
        evidence_identities=tuple(proof.content_identity for proof in proofs),
        proofs=proofs,
        completed_routes=tuple(proof.route for proof in proofs),
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
        offline_observations=offline_observations,
    )


def qualify_offline_exposure_observations(
    *,
    expected_source_sha: str,
    source_root: Path,
    reference: PrivateCredentialReference,
    capability: PrivateCredentialCapabilityEvidence,
    account_identity: ContentIdentity,
    observations: tuple[OfflineExposureObservation, ...],
) -> OfflineExposureQualificationResult:
    """Validate two supplied observations; a PASSED result is offline only."""
    source: SourceTree | None = None
    requests: tuple[OfflineExposureRequest, ...] = ()
    proofs: tuple[SanitizedExposureObservation, ...] = ()
    count = len(observations) if type(observations) is tuple else 0
    bound_reference: PrivateCredentialReference | None = None
    bound_capability: PrivateCredentialCapabilityEvidence | None = None
    bound_account: ContentIdentity | None = None
    try:
        source = _source_before(source_root, expected_source_sha)
        try:
            requested = exact_offline_exposure_requests(reference, capability, account_identity)
        except (EvidenceError, AttributeError, TypeError, ValueError):
            raise _OfflineFailure(OfflineExposureReason.BINDING_INVALID) from None
        requests = requested
        bound_reference = reference
        bound_capability = capability
        bound_account = account_identity
        if type(observations) is not tuple or len(observations) != 2:
            raise _OfflineFailure(OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID)
        observed_times: list[datetime] = []
        for observation, request in zip(observations, requests, strict=True):
            if (
                type(observation) is not OfflineExposureObservation
                or observation.route is not request.route
                or observation.request_identity != request.content_identity
                or type(observation.observed_at) is not datetime
                or observation.observed_at.tzinfo is None
            ):
                raise _OfflineFailure(OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID)
            observed_times.append(observation.observed_at)
            proofs = (*proofs, _proof(observation, request))
        if (
            observed_times[1] < observed_times[0]
            or observed_times[1] - observed_times[0] > timedelta(seconds=30)
            or any(
                moment < capability.observed_at
                or moment - capability.observed_at > timedelta(seconds=30)
                for moment in observed_times
            )
        ):
            raise _OfflineFailure(OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID)
        try:
            after = inspect_source(source_root)
        except (ReleaseError, OSError):
            raise _OfflineFailure(OfflineExposureReason.SOURCE_INVALID) from None
        if after != source or not after.clean:
            raise _OfflineFailure(OfflineExposureReason.SOURCE_INVALID)
        return _result(
            status=OfflineExposureStatus.PASSED,
            reason=OfflineExposureReason.OFFLINE_EXPOSURE_CONTRACT_QUALIFIED,
            source=source,
            reference=bound_reference,
            capability=bound_capability,
            account_identity=bound_account,
            requests=requests,
            proofs=proofs,
            offline_observations=count,
        )
    except _OfflineFailure as failure:
        return _result(
            status=OfflineExposureStatus.FAILED,
            reason=failure.reason,
            source=source if failure.reason is not OfflineExposureReason.SOURCE_INVALID else None,
            reference=bound_reference,
            capability=bound_capability,
            account_identity=bound_account,
            requests=requests,
            proofs=proofs,
            offline_observations=count,
        )
