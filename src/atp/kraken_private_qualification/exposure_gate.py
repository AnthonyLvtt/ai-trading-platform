"""One source-bound operator path: key info, BalanceEx, TradeVolume.

This module is preparation only. No caller, CLI, or OMS runtime path executes it.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.exposure_transport import KrakenExposureTransport
from atp.exchange.kraken.private import (
    KrakenPrivateError,
    KrakenPrivateReadRoute,
    MonotonicNonceProvider,
    parse_api_key_info,
)
from atp.exchange.kraken.private_credentials import (
    EphemeralKrakenCredential,
    KrakenCredentialError,
)
from atp.exchange.kraken.private_transport import (
    KrakenPrivateHTTPObservation,
    KrakenPrivateTransport,
    KrakenPrivateTransportError,
    api_key_info_request,
)
from atp.exchange.read_only import EvidenceError
from atp.kraken_private_qualification.exposure_connectivity import (
    _source,
    qualify_exposure_connectivity,
)
from atp.kraken_private_qualification.exposure_connectivity_model import (
    ExposureConnectivityStatus,
)
from atp.kraken_private_qualification.exposure_gate_model import (
    ExposureGateReason,
    ExposureGateResult,
    ExposureGateStatus,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity

CredentialLoader = Callable[[], EphemeralKrakenCredential]
Clock = Callable[[], datetime]


class _GateFailure(ValueError):
    def __init__(self, reason: ExposureGateReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def account_identity_from_iiban(iiban: str) -> ContentIdentity:
    """Hash an independently known Spot account IIBAN; never persist its text."""
    if type(iiban) is not str or re.fullmatch(r"[A-Za-z0-9 -]{8,64}", iiban) is None:
        raise EvidenceError("EXPOSURE_ACCOUNT_IIBAN_INVALID")
    normalized = re.sub(r"[ -]", "", iiban).upper()
    if not 8 <= len(normalized) <= 40:
        raise EvidenceError("EXPOSURE_ACCOUNT_IIBAN_INVALID")
    return ContentIdentity.from_canonical(("ATP/KRAKEN/SPOT/IIBAN/v1", normalized))


def _binding(
    observation: KrakenPrivateHTTPObservation,
    credential: EphemeralKrakenCredential,
    expected_account_identity: ContentIdentity,
    started_at: datetime,
) -> tuple[ContentIdentity, object]:
    if (
        type(observation) is not KrakenPrivateHTTPObservation
        or observation.route is not KrakenPrivateReadRoute.API_KEY_INFO
        or type(observation.request_started_at) is not datetime
        or observation.request_started_at.tzinfo is None
        or type(observation.observed_at) is not datetime
        or observation.observed_at.tzinfo is None
        or not started_at <= observation.request_started_at <= observation.observed_at
        or observation.observed_at - started_at > timedelta(seconds=30)
    ):
        raise _GateFailure(ExposureGateReason.KEY_INFO_INVALID)
    payload = observation.payload
    if (
        type(payload) is not dict
        or set(payload) != {"error", "result"}
        or type(payload.get("error")) is not list
        or payload["error"]
        or type(payload.get("result")) is not dict
    ):
        raise _GateFailure(ExposureGateReason.KEY_INFO_INVALID)
    result = payload["result"]
    api_key = result.get("apiKey")
    iiban = result.get("iban")
    if type(api_key) is not str or type(iiban) is not str:
        raise _GateFailure(ExposureGateReason.ACCOUNT_BINDING_INVALID)
    try:
        key_matches = hmac.compare_digest(api_key.encode("ascii"), credential.api_key_bytes())
        account_identity = account_identity_from_iiban(iiban)
    except (UnicodeError, EvidenceError, KrakenCredentialError):
        raise _GateFailure(ExposureGateReason.ACCOUNT_BINDING_INVALID) from None
    if not key_matches or account_identity != expected_account_identity:
        raise _GateFailure(ExposureGateReason.ACCOUNT_BINDING_INVALID)
    return account_identity, payload


def _result(
    *,
    status: ExposureGateStatus,
    reason: ExposureGateReason,
    source: SourceTree | None,
    account_identity: ContentIdentity | None,
    reference_identity: ContentIdentity | None,
    capability_identity: ContentIdentity | None,
    exposure_result_identity: ContentIdentity | None,
    routes: tuple[str, ...],
    calls: int,
    started_at: datetime,
    completed_at: datetime,
) -> ExposureGateResult:
    return ExposureGateResult(
        status=status,
        reason_code=reason,
        venue=VenueId.KRAKEN,
        account_identity=account_identity,
        credential_reference_identity=reference_identity,
        capability_identity=capability_identity,
        exposure_result_identity=exposure_result_identity,
        completed_routes=routes,
        total_private_network_calls=calls,
        started_at=started_at,
        completed_at=completed_at,
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
    )


def qualify_exposure_operator_gate(
    *,
    expected_source_sha: str,
    source_root: Path,
    expected_account_identity: ContentIdentity,
    credential_loader: CredentialLoader,
    key_info_transport: KrakenPrivateTransport,
    exposure_transport: KrakenExposureTransport,
    clock: Clock = lambda: datetime.now(UTC),
) -> ExposureGateResult:
    """Compose the only prepared sequence; real execution needs separate approval."""
    started_at = clock()
    credential: EphemeralKrakenCredential | None = None
    source: SourceTree | None = None
    account_identity: ContentIdentity | None = None
    reference_identity: ContentIdentity | None = None
    capability_identity: ContentIdentity | None = None
    exposure_result_identity: ContentIdentity | None = None
    routes: tuple[str, ...] = ()
    calls = 0
    try:
        try:
            source = _source(source_root, expected_source_sha)
        except ValueError:
            raise _GateFailure(ExposureGateReason.SOURCE_INVALID) from None
        if type(expected_account_identity) is not ContentIdentity:
            raise _GateFailure(ExposureGateReason.ACCOUNT_BINDING_INVALID)
        try:
            credential = credential_loader()
        except KrakenCredentialError:
            raise _GateFailure(ExposureGateReason.CREDENTIAL_INVALID) from None
        if type(credential) is not EphemeralKrakenCredential:
            raise _GateFailure(ExposureGateReason.CREDENTIAL_INVALID)
        reference_identity = credential.reference.content_identity
        request = api_key_info_request(credential)
        nonce_provider = MonotonicNonceProvider(reference_identity)
        try:
            observation = key_info_transport.post(request, credential, nonce_provider)
        except KrakenPrivateTransportError as exc:
            calls += int(exc.network_call_performed)
            raise _GateFailure(ExposureGateReason.KEY_INFO_INVALID) from None
        calls += 1
        account_identity, payload = _binding(
            observation, credential, expected_account_identity, started_at
        )
        routes = (KrakenPrivateReadRoute.API_KEY_INFO.value,)
        try:
            capability = parse_api_key_info(payload, credential.reference, observation.observed_at)
        except (KrakenPrivateError, EvidenceError) as exc:
            reason = (
                ExposureGateReason.LEAST_PRIVILEGE_REQUIRED
                if str(exc) == "KRAKEN_LEAST_PRIVILEGE_REQUIRED"
                else ExposureGateReason.KEY_INFO_INVALID
            )
            raise _GateFailure(reason) from None
        capability_identity = capability.content_identity
        exposure = qualify_exposure_connectivity(
            expected_source_sha=expected_source_sha,
            source_root=source_root,
            credential_loader=lambda: credential,
            capability=capability,
            account_identity=account_identity,
            transport=exposure_transport,
            clock=clock,
        )
        calls += exposure.private_network_calls
        routes += tuple(route.value for route in exposure.completed_routes)
        exposure_result_identity = exposure.content_identity
        if exposure.status is not ExposureConnectivityStatus.PASSED:
            raise _GateFailure(ExposureGateReason.EXPOSURE_INVALID)
        try:
            after = inspect_source(source_root)
        except (ReleaseError, OSError):
            raise _GateFailure(ExposureGateReason.SOURCE_INVALID) from None
        if (
            after != source
            or not after.clean
            or exposure.source_identity != source.content_identity
        ):
            raise _GateFailure(ExposureGateReason.SOURCE_INVALID)
        return _result(
            status=ExposureGateStatus.PASSED,
            reason=ExposureGateReason.QUALIFIED,
            source=source,
            account_identity=account_identity,
            reference_identity=reference_identity,
            capability_identity=capability_identity,
            exposure_result_identity=exposure_result_identity,
            routes=routes,
            calls=calls,
            started_at=started_at,
            completed_at=clock(),
        )
    except _GateFailure as exc:
        return _result(
            status=ExposureGateStatus.FAILED,
            reason=exc.reason,
            source=None,
            account_identity=account_identity,
            reference_identity=reference_identity,
            capability_identity=capability_identity,
            exposure_result_identity=exposure_result_identity,
            routes=routes,
            calls=calls,
            started_at=started_at,
            completed_at=clock(),
        )
    finally:
        if credential is not None:
            credential.close()
