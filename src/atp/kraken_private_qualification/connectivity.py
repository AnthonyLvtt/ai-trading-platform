"""Explicit, source-bound Kraken private read-only connectivity qualification."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.private import (
    KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST,
    KrakenPrivateError,
    MonotonicNonceProvider,
    account_wide_open_orders_request,
    parse_api_key_info,
    parse_balances,
    parse_open_orders,
)
from atp.exchange.kraken.private_credentials import (
    EphemeralKrakenCredential,
    KrakenCredentialError,
)
from atp.exchange.kraken.private_transport import (
    KrakenPrivateHTTPObservation,
    KrakenPrivateReadRequest,
    KrakenPrivateTransport,
    KrakenPrivateTransportError,
    api_key_info_request,
    balance_request,
)
from atp.exchange.private_contracts import (
    AccountBalanceEvidence,
    AccountOpenOrdersEvidence,
    PrivateCredentialCapabilityEvidence,
)
from atp.exchange.read_only import EvidenceError, verify_record
from atp.kraken_private_qualification.connectivity_model import (
    KrakenPrivateConnectivityReason,
    KrakenPrivateConnectivityResult,
    KrakenPrivateConnectivityStatus,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity

CredentialLoader = Callable[[], EphemeralKrakenCredential]
Clock = Callable[[], datetime]


@dataclass(slots=True)
class _RunState:
    started_at: datetime
    calls: int = 0
    routes: tuple[str, ...] = ()
    reference_identity: ContentIdentity | None = None
    evidence_identities: tuple[ContentIdentity, ...] = ()


class _QualificationFailure(ValueError):
    def __init__(self, reason: KrakenPrivateConnectivityReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _source_before(root: Path, expected_sha: str) -> SourceTree:
    if re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID)
    try:
        source = inspect_source(root)
    except (ReleaseError, OSError):
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID) from None
    if (
        not source.clean
        or source.source_branch != "main"
        or source.source_commit_sha != expected_sha
    ):
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID)
    return source


def _call(
    state: _RunState,
    transport: KrakenPrivateTransport,
    request: KrakenPrivateReadRequest,
    credential: EphemeralKrakenCredential,
    nonce_provider: MonotonicNonceProvider,
) -> KrakenPrivateHTTPObservation:
    try:
        observation = transport.post(request, credential, nonce_provider)
    except KrakenPrivateTransportError as exc:
        state.calls += int(exc.network_call_performed)
        try:
            reason = KrakenPrivateConnectivityReason(str(exc))
        except ValueError:
            reason = KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_NETWORK_FAILURE
        raise _QualificationFailure(reason) from None
    state.calls += 1
    if observation.route is not request.route:
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_RESPONSE_INVALID)
    state.routes = (*state.routes, observation.route.value)
    return observation


def _payload_result(observation: KrakenPrivateHTTPObservation) -> dict[str, object]:
    payload = observation.payload
    if type(payload) is not dict or type(payload.get("error")) is not list:
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_RESPONSE_INVALID)
    errors = payload["error"]
    if errors:
        if not all(type(item) is str for item in errors):
            raise _QualificationFailure(
                KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_RESPONSE_INVALID
            )
        known = set(errors)
        if known & {"EAPI:Invalid key", "EAPI:Invalid signature"}:
            reason = KrakenPrivateConnectivityReason.KRAKEN_AUTH_REJECTED
        elif "EAPI:Invalid nonce" in known:
            reason = KrakenPrivateConnectivityReason.KRAKEN_NONCE_REJECTED
        elif known & {"EGeneral:Permission denied", "EAccount:Invalid permissions"}:
            reason = KrakenPrivateConnectivityReason.KRAKEN_PERMISSION_REJECTED
        else:
            reason = KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_API_REJECTED
        raise _QualificationFailure(reason)
    if set(payload) != {"error", "result"} or type(payload.get("result")) is not dict:
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_RESPONSE_INVALID)
    return payload


def _validate_evidence(
    capability: PrivateCredentialCapabilityEvidence,
    balances: AccountBalanceEvidence,
    request_identity: ContentIdentity,
    orders: AccountOpenOrdersEvidence,
) -> None:
    if (
        not verify_record(capability, PrivateCredentialCapabilityEvidence)
        or not verify_record(balances, AccountBalanceEvidence)
        or not verify_record(orders, AccountOpenOrdersEvidence)
        or balances.capability_identity != capability.content_identity
        or orders.capability_identity != capability.content_identity
        or orders.request_identity != request_identity
        or capability.submission_authorized is not False
        or capability.side_effect_performed is not False
        or balances.side_effect_performed is not False
        or orders.side_effect_performed is not False
        or orders.scope != "ACCOUNT_WIDE"
        or orders.complete is not True
        or abs((balances.observed_at - capability.observed_at).total_seconds()) > 60
        or abs((orders.observed_at - capability.observed_at).total_seconds()) > 60
    ):
        raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_EVIDENCE_INVALID)


def _result(
    state: _RunState,
    status: KrakenPrivateConnectivityStatus,
    reason: KrakenPrivateConnectivityReason,
    completed_at: datetime,
    source: SourceTree | None,
) -> KrakenPrivateConnectivityResult:
    return KrakenPrivateConnectivityResult(
        status=status,
        reason_code=reason,
        venue=VenueId.KRAKEN,
        credential_reference_identity=state.reference_identity,
        evidence_identities=state.evidence_identities,
        completed_routes=state.routes,
        route_allowlist=tuple(sorted(KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST)),
        started_at=state.started_at,
        completed_at=completed_at,
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
        private_network_calls=state.calls,
    )


def qualify_private_connectivity(
    *,
    expected_source_sha: str,
    source_root: Path,
    credential_loader: CredentialLoader,
    transport: KrakenPrivateTransport,
    clock: Clock = lambda: datetime.now(UTC),
) -> KrakenPrivateConnectivityResult:
    """Run exactly three private reads after validating exact clean main."""
    state = _RunState(clock())
    credential: EphemeralKrakenCredential | None = None
    try:
        source = _source_before(source_root, expected_source_sha)
        try:
            credential = credential_loader()
        except KrakenCredentialError as exc:
            reason = (
                KrakenPrivateConnectivityReason.KRAKEN_CREDENTIAL_UNAVAILABLE
                if str(exc) == "KRAKEN_CREDENTIAL_UNAVAILABLE"
                else KrakenPrivateConnectivityReason.KRAKEN_CREDENTIAL_INVALID
            )
            raise _QualificationFailure(reason) from None
        state.reference_identity = credential.reference.content_identity
        nonce_provider = MonotonicNonceProvider(credential.reference.content_identity)

        key_observation = _call(
            state, transport, api_key_info_request(credential), credential, nonce_provider
        )
        key_payload = _payload_result(key_observation)
        try:
            capability = parse_api_key_info(
                key_payload, credential.reference, key_observation.observed_at
            )
        except (KrakenPrivateError, EvidenceError) as exc:
            reason = (
                KrakenPrivateConnectivityReason.KRAKEN_LEAST_PRIVILEGE_REQUIRED
                if str(exc) == "KRAKEN_LEAST_PRIVILEGE_REQUIRED"
                else KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_EVIDENCE_INVALID
            )
            raise _QualificationFailure(reason) from None

        balance_observation = _call(
            state, transport, balance_request(credential), credential, nonce_provider
        )
        balance_payload = _payload_result(balance_observation)
        try:
            balances = parse_balances(
                balance_payload,
                credential.reference,
                capability,
                balance_observation.observed_at,
            )
        except (KrakenPrivateError, EvidenceError):
            raise _QualificationFailure(
                KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_EVIDENCE_INVALID
            ) from None

        open_orders_request = account_wide_open_orders_request(credential.reference)
        orders_observation = _call(
            state, transport, open_orders_request, credential, nonce_provider
        )
        orders_payload = _payload_result(orders_observation)
        try:
            orders = parse_open_orders(
                orders_payload,
                credential.reference,
                capability,
                open_orders_request,
                orders_observation.observed_at,
            )
        except (KrakenPrivateError, EvidenceError):
            raise _QualificationFailure(
                KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_EVIDENCE_INVALID
            ) from None
        _validate_evidence(capability, balances, open_orders_request.content_identity, orders)
        state.evidence_identities = (
            capability.content_identity,
            balances.content_identity,
            open_orders_request.content_identity,
            orders.content_identity,
        )
        try:
            after = inspect_source(source_root)
        except (ReleaseError, OSError):
            raise _QualificationFailure(
                KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID
            ) from None
        if after != source or not after.clean:
            raise _QualificationFailure(KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID)
        return _result(
            state,
            KrakenPrivateConnectivityStatus.PASSED,
            KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_CONNECTIVITY_QUALIFIED,
            clock(),
            source,
        )
    except _QualificationFailure as exc:
        return _result(
            state,
            KrakenPrivateConnectivityStatus.FAILED,
            exc.reason,
            clock(),
            None,
        )
    finally:
        if credential is not None:
            credential.close()
