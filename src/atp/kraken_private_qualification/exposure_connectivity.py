"""Explicit two-route exposure qualification; never invoked by ordinary runtime."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.exposure_transport import (
    KrakenExposureHTTPObservation,
    KrakenExposureTransport,
    KrakenExposureTransportError,
)
from atp.exchange.kraken.private import MonotonicNonceProvider
from atp.exchange.kraken.private_credentials import (
    EphemeralKrakenCredential,
    KrakenCredentialError,
)
from atp.exchange.private_contracts import PrivateCredentialCapabilityEvidence
from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_private_qualification.exposure_connectivity_model import (
    ExposureConnectivityReason,
    ExposureConnectivityResult,
    ExposureConnectivityStatus,
)
from atp.kraken_private_qualification.exposure_observation import (
    _OfflineFailure,
    _proof,
    exact_offline_exposure_requests,
)
from atp.kraken_private_qualification.exposure_observation_model import (
    OfflineExposureObservation,
    OfflineExposureReason,
    OfflineExposureRequest,
    OfflineExposureRoute,
    OfflineExposureValueKind,
    SanitizedExposureObservation,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity

CredentialLoader = Callable[[], EphemeralKrakenCredential]
Clock = Callable[[], datetime]


class _Failure(ValueError):
    def __init__(self, reason: ExposureConnectivityReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _source(root: Path, expected_sha: str) -> SourceTree:
    if type(expected_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
        raise _Failure(ExposureConnectivityReason.SOURCE_INVALID)
    try:
        source = inspect_source(root)
    except (ReleaseError, OSError):
        raise _Failure(ExposureConnectivityReason.SOURCE_INVALID) from None
    if (
        not source.clean
        or source.source_branch != "main"
        or source.source_commit_sha != expected_sha
    ):
        raise _Failure(ExposureConnectivityReason.SOURCE_INVALID)
    return source


def _source_after(root: Path, source: SourceTree) -> None:
    try:
        after = inspect_source(root)
    except (ReleaseError, OSError):
        raise _Failure(ExposureConnectivityReason.SOURCE_INVALID) from None
    if after != source or not after.clean:
        raise _Failure(ExposureConnectivityReason.SOURCE_INVALID)


def _checked_observation(
    observation: KrakenExposureHTTPObservation,
    request: OfflineExposureRequest,
    capability: PrivateCredentialCapabilityEvidence,
    previous_at: datetime | None,
) -> SanitizedExposureObservation:
    if (
        type(observation) is not KrakenExposureHTTPObservation
        or observation.route is not request.route
        or observation.request_identity != request.content_identity
        or type(observation.request_started_at) is not datetime
        or observation.request_started_at.tzinfo is None
        or type(observation.observed_at) is not datetime
        or observation.observed_at.tzinfo is None
        or not capability.observed_at <= observation.request_started_at <= observation.observed_at
        or observation.observed_at - capability.observed_at > timedelta(seconds=30)
        or (previous_at is not None and observation.request_started_at < previous_at)
    ):
        raise _Failure(ExposureConnectivityReason.RESPONSE_INVALID)
    payload = observation.payload
    if type(payload) is dict and type(payload.get("error")) is list and payload["error"]:
        if all(type(error) is str for error in payload["error"]):
            raise _Failure(ExposureConnectivityReason.API_REJECTED)
        raise _Failure(ExposureConnectivityReason.EVIDENCE_INVALID)
    try:
        return _proof(
            OfflineExposureObservation(
                observation.route,
                observation.request_identity,
                observation.payload,
                observation.observed_at,
            ),
            request,
        )
    except _OfflineFailure as exc:
        reason = ExposureConnectivityReason.EVIDENCE_INVALID
        if exc.reason is OfflineExposureReason.BALANCE_ASSET_UNSUPPORTED:
            reason = ExposureConnectivityReason.BALANCE_ASSET_UNSUPPORTED
        elif exc.reason is OfflineExposureReason.BALANCE_FIELDS_INCOMPLETE:
            reason = ExposureConnectivityReason.BALANCE_FIELDS_INCOMPLETE
        elif exc.reason is OfflineExposureReason.BALANCE_EUR_MISSING:
            reason = ExposureConnectivityReason.BALANCE_EUR_MISSING
        raise _Failure(reason) from None


def _result(
    *,
    status: ExposureConnectivityStatus,
    reason: ExposureConnectivityReason,
    started_at: datetime,
    completed_at: datetime,
    source: SourceTree | None,
    reference_identity: ContentIdentity | None,
    capability_identity: ContentIdentity | None,
    account_identity: ContentIdentity | None,
    requests: tuple[ContentIdentity, ...],
    proofs: tuple[SanitizedExposureObservation, ...],
    routes: tuple[OfflineExposureRoute, ...],
    calls: int,
) -> ExposureConnectivityResult:
    return ExposureConnectivityResult(
        status=status,
        reason_code=reason,
        venue=VenueId.KRAKEN,
        credential_reference_identity=reference_identity,
        capability_identity=capability_identity,
        account_identity=account_identity,
        request_identities=requests,
        proofs=proofs,
        completed_routes=routes,
        started_at=started_at,
        completed_at=completed_at,
        private_network_calls=calls,
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
    )


def qualify_exposure_connectivity(
    *,
    expected_source_sha: str,
    source_root: Path,
    credential_loader: CredentialLoader,
    capability: PrivateCredentialCapabilityEvidence,
    account_identity: ContentIdentity,
    transport: KrakenExposureTransport,
    clock: Clock = lambda: datetime.now(UTC),
) -> ExposureConnectivityResult:
    """Make exactly two bounded reads after source and capability preflight.

    Calling this function with an HTTP transport requires separate operational
    authorization. A PASSED result from a simulated transport is not a real
    observation and cannot enable OMS runtime PASS.
    """
    started_at = clock()
    source: SourceTree | None = None
    credential: EphemeralKrakenCredential | None = None
    reference_identity: ContentIdentity | None = None
    capability_identity: ContentIdentity | None = None
    bound_account: ContentIdentity | None = None
    requests: tuple[ContentIdentity, ...] = ()
    proofs: tuple[SanitizedExposureObservation, ...] = ()
    routes: tuple[OfflineExposureRoute, ...] = ()
    calls = 0
    try:
        source = _source(source_root, expected_source_sha)
        if (
            not verify_record(capability, PrivateCredentialCapabilityEvidence)
            or capability.venue is not VenueId.KRAKEN
            or capability.funds_query is not True
            or capability.trading_capability_absent is not True
            or capability.withdrawal_capability_absent is not True
            or capability.submission_authorized is not False
            or not capability.observed_at <= started_at
            or started_at - capability.observed_at > timedelta(seconds=30)
            or type(account_identity) is not ContentIdentity
        ):
            raise _Failure(ExposureConnectivityReason.BINDING_INVALID)
        try:
            credential = credential_loader()
        except KrakenCredentialError:
            raise _Failure(ExposureConnectivityReason.CREDENTIAL_INVALID) from None
        if type(credential) is not EphemeralKrakenCredential:
            raise _Failure(ExposureConnectivityReason.CREDENTIAL_INVALID)
        reference_identity = credential.reference.content_identity
        if capability.credential_reference_identity != reference_identity:
            raise _Failure(ExposureConnectivityReason.BINDING_INVALID)
        capability_identity = capability.content_identity
        bound_account = account_identity
        try:
            exact_requests = exact_offline_exposure_requests(
                credential.reference, capability, account_identity
            )
        except EvidenceError:
            raise _Failure(ExposureConnectivityReason.BINDING_INVALID) from None
        requests = tuple(request.content_identity for request in exact_requests)
        nonce_provider = MonotonicNonceProvider(reference_identity)
        previous_at: datetime | None = None
        for request in exact_requests:
            try:
                observation = transport.post(request, credential, nonce_provider)
            except KrakenExposureTransportError as exc:
                calls += int(exc.network_call_performed)
                raise _Failure(ExposureConnectivityReason.NETWORK_FAILURE) from None
            calls += 1
            proof = _checked_observation(observation, request, capability, previous_at)
            routes = (*routes, request.route)
            proofs = (*proofs, proof)
            previous_at = observation.observed_at
            if proof.value_kind is OfflineExposureValueKind.EUR_BALANCE_NOT_OBSERVED:
                _source_after(source_root, source)
                return _result(
                    status=ExposureConnectivityStatus.INCOMPLETE,
                    reason=ExposureConnectivityReason.EUR_BALANCE_NOT_OBSERVED,
                    started_at=started_at,
                    completed_at=clock(),
                    source=source,
                    reference_identity=reference_identity,
                    capability_identity=capability_identity,
                    account_identity=bound_account,
                    requests=requests,
                    proofs=proofs,
                    routes=routes,
                    calls=calls,
                )
        _source_after(source_root, source)
        return _result(
            status=ExposureConnectivityStatus.PASSED,
            reason=ExposureConnectivityReason.QUALIFIED,
            started_at=started_at,
            completed_at=clock(),
            source=source,
            reference_identity=reference_identity,
            capability_identity=capability_identity,
            account_identity=bound_account,
            requests=requests,
            proofs=proofs,
            routes=routes,
            calls=calls,
        )
    except _Failure as exc:
        return _result(
            status=ExposureConnectivityStatus.FAILED,
            reason=exc.reason,
            started_at=started_at,
            completed_at=clock(),
            source=None,
            reference_identity=reference_identity,
            capability_identity=capability_identity,
            account_identity=bound_account,
            requests=requests,
            proofs=proofs,
            routes=routes,
            calls=calls,
        )
    finally:
        if credential is not None:
            credential.close()
