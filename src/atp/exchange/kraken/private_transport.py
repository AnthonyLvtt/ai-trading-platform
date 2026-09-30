"""Closed HTTPS transport for three Kraken private read-only observations."""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from typing import Protocol

from atp.exchange.kraken.private import (
    KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST,
    KrakenAccountWideOpenOrdersRequest,
    KrakenPrivateError,
    KrakenPrivateReadRoute,
    MonotonicNonceProvider,
    sign_private_read_request,
)
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.exchange.read_only import EvidenceRecord
from atp.shared.identity import ContentIdentity

KRAKEN_PRIVATE_HOST = "api.kraken.com"
KRAKEN_PRIVATE_TIMEOUT_SECONDS = 10
KRAKEN_PRIVATE_MAX_RESPONSE_BYTES = 2_000_000


class KrakenPrivateTransportError(ValueError):
    """A sanitized private transport failure."""

    def __init__(self, code: str, *, network_call_performed: bool = False) -> None:
        super().__init__(code)
        self.network_call_performed = network_call_performed


@dataclass(frozen=True, slots=True)
class KrakenApiKeyInfoRequest(EvidenceRecord):
    credential_reference_identity: ContentIdentity
    route: KrakenPrivateReadRoute = KrakenPrivateReadRoute.API_KEY_INFO
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.credential_reference_identity) is not ContentIdentity
            or self.route is not KrakenPrivateReadRoute.API_KEY_INFO
            or self.parameters != ()
        ):
            raise KrakenPrivateTransportError("KRAKEN_PRIVATE_REQUEST_INVALID")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class KrakenBalanceRequest(EvidenceRecord):
    credential_reference_identity: ContentIdentity
    route: KrakenPrivateReadRoute = KrakenPrivateReadRoute.BALANCE
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.credential_reference_identity) is not ContentIdentity
            or self.route is not KrakenPrivateReadRoute.BALANCE
            or self.parameters != ()
        ):
            raise KrakenPrivateTransportError("KRAKEN_PRIVATE_REQUEST_INVALID")
        EvidenceRecord.__post_init__(self)


type KrakenPrivateReadRequest = (
    KrakenApiKeyInfoRequest | KrakenBalanceRequest | KrakenAccountWideOpenOrdersRequest
)


@dataclass(frozen=True, slots=True)
class KrakenPrivateHTTPObservation:
    """Internal response value. Raw payloads are deliberately not EvidenceRecords."""

    route: KrakenPrivateReadRoute
    payload: object
    request_started_at: datetime
    observed_at: datetime


class KrakenPrivateTransport(Protocol):
    def post(
        self,
        request: KrakenPrivateReadRequest,
        credential: EphemeralKrakenCredential,
        nonce_provider: MonotonicNonceProvider,
    ) -> KrakenPrivateHTTPObservation: ...


def api_key_info_request(credential: EphemeralKrakenCredential) -> KrakenApiKeyInfoRequest:
    return KrakenApiKeyInfoRequest(credential.reference.content_identity)


def balance_request(credential: EphemeralKrakenCredential) -> KrakenBalanceRequest:
    return KrakenBalanceRequest(credential.reference.content_identity)


def _route(request: KrakenPrivateReadRequest) -> KrakenPrivateReadRoute:
    if type(request) is KrakenApiKeyInfoRequest:
        return KrakenPrivateReadRoute.API_KEY_INFO
    if type(request) is KrakenBalanceRequest:
        return KrakenPrivateReadRoute.BALANCE
    if type(request) is KrakenAccountWideOpenOrdersRequest:
        return KrakenPrivateReadRoute.OPEN_ORDERS
    raise KrakenPrivateTransportError("KRAKEN_PRIVATE_ROUTE_FORBIDDEN")


class KrakenPrivateHTTPTransport:
    """POST-only, non-redirecting transport with no free host, method, or route API."""

    __slots__ = ("_clock",)

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    def post(
        self,
        request: KrakenPrivateReadRequest,
        credential: EphemeralKrakenCredential,
        nonce_provider: MonotonicNonceProvider,
    ) -> KrakenPrivateHTTPObservation:
        route = _route(request)
        if (
            route.value not in KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
            or request.parameters != ()
            or request.credential_reference_identity != credential.reference.content_identity
            or nonce_provider.reference_identity != credential.reference.content_identity
        ):
            raise KrakenPrivateTransportError("KRAKEN_PRIVATE_REQUEST_INVALID")
        try:
            nonce = nonce_provider.next()
            body, signature = sign_private_read_request(
                route,
                nonce,
                credential.secret_buffer(),
            )
        except KrakenPrivateError:
            raise KrakenPrivateTransportError("KRAKEN_NONCE_OR_SIGNING_FAILURE") from None
        try:
            api_key = credential.api_key_bytes().decode("ascii")
        except UnicodeError:
            raise KrakenPrivateTransportError("KRAKEN_CREDENTIAL_INVALID") from None
        started_at = self._clock()
        connection: HTTPSConnection | None = None
        network_call_performed = False
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            connection = HTTPSConnection(
                KRAKEN_PRIVATE_HOST,
                port=443,
                timeout=KRAKEN_PRIVATE_TIMEOUT_SECONDS,
                context=context,
            )
            network_call_performed = True
            connection.request(
                "POST",
                route.value,
                body=body,
                headers={
                    "Accept": "application/json",
                    "API-Key": api_key,
                    "API-Sign": signature,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "ATP-Kraken-Private-Qualification/1",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise KrakenPrivateTransportError(
                    "KRAKEN_PRIVATE_HTTP_FAILURE", network_call_performed=True
                )
            encoding = response.getheader("Content-Encoding")
            if encoding not in (None, "", "identity"):
                raise KrakenPrivateTransportError(
                    "KRAKEN_PRIVATE_RESPONSE_INVALID", network_call_performed=True
                )
            body_bytes = response.read(KRAKEN_PRIVATE_MAX_RESPONSE_BYTES + 1)
            if len(body_bytes) > KRAKEN_PRIVATE_MAX_RESPONSE_BYTES:
                raise KrakenPrivateTransportError(
                    "KRAKEN_PRIVATE_RESPONSE_TOO_LARGE", network_call_performed=True
                )
            try:
                payload = json.loads(body_bytes)
            except (UnicodeError, ValueError, RecursionError):
                raise KrakenPrivateTransportError(
                    "KRAKEN_PRIVATE_JSON_INVALID", network_call_performed=True
                ) from None
            return KrakenPrivateHTTPObservation(route, payload, started_at, self._clock())
        except KrakenPrivateTransportError as exc:
            if exc.network_call_performed:
                raise
            raise KrakenPrivateTransportError(str(exc), network_call_performed=True) from None
        except TimeoutError:
            raise KrakenPrivateTransportError(
                "KRAKEN_PRIVATE_TIMEOUT", network_call_performed=network_call_performed
            ) from None
        except (OSError, HTTPException, ssl.SSLError):
            raise KrakenPrivateTransportError(
                "KRAKEN_PRIVATE_NETWORK_FAILURE",
                network_call_performed=network_call_performed,
            ) from None
        finally:
            if connection is not None:
                connection.close()
            api_key = ""
            signature = ""
            body = ""
