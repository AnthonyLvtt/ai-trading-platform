"""Bounded transport for future Kraken exposure observations.

The module is intentionally separate from the existing private-read allowlist.
It exposes only BalanceEx and TradeVolume with exact parameters, fixed host,
POST-only HTTPS, no redirects, and no economic routes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlencode

from atp.exchange.kraken.private import MonotonicNonceProvider
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.kraken_private_qualification.exposure_observation_model import (
    OfflineExposureRequest,
    OfflineExposureRoute,
)
from atp.shared.identity import ContentIdentity

KRAKEN_EXPOSURE_HOST = "api.kraken.com"
KRAKEN_EXPOSURE_TIMEOUT_SECONDS = 10
KRAKEN_EXPOSURE_MAX_RESPONSE_BYTES = 2_000_000
KRAKEN_EXPOSURE_ROUTE_ALLOWLIST = frozenset(route.value for route in OfflineExposureRoute)


class KrakenExposureTransportError(ValueError):
    def __init__(self, code: str, *, network_call_performed: bool = False) -> None:
        super().__init__(code)
        self.network_call_performed = network_call_performed


@dataclass(frozen=True, slots=True)
class KrakenExposureHTTPObservation:
    route: OfflineExposureRoute
    request_identity: ContentIdentity
    payload: object
    request_started_at: datetime
    observed_at: datetime


def _exact_parameters(request: OfflineExposureRequest) -> tuple[tuple[str, str], ...]:
    expected: tuple[tuple[str, str], ...]
    if request.route is OfflineExposureRoute.BALANCE_EX:
        expected = ()
    elif request.route is OfflineExposureRoute.TRADE_VOLUME:
        expected = (("pair", "XXBTZEUR"),)
    else:
        raise KrakenExposureTransportError("KRAKEN_EXPOSURE_ROUTE_FORBIDDEN")
    if (
        request.host != KRAKEN_EXPOSURE_HOST
        or request.method != "POST"
        or request.parameters != expected
        or request.route.value not in KRAKEN_EXPOSURE_ROUTE_ALLOWLIST
    ):
        raise KrakenExposureTransportError("KRAKEN_EXPOSURE_REQUEST_INVALID")
    return expected


def sign_exposure_request(
    request: OfflineExposureRequest,
    nonce: int,
    secret: str | bytes | bytearray,
) -> tuple[str, str]:
    parameters = _exact_parameters(request)
    if type(nonce) is not int or not 0 <= nonce < 2**64:
        raise KrakenExposureTransportError("KRAKEN_EXPOSURE_NONCE_INVALID")
    body = urlencode((("nonce", str(nonce)), *parameters))
    try:
        decoded = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError):
        raise KrakenExposureTransportError("KRAKEN_EXPOSURE_SECRET_INVALID") from None
    if not decoded:
        raise KrakenExposureTransportError("KRAKEN_EXPOSURE_SECRET_INVALID")
    digest = hashlib.sha256(f"{nonce}{body}".encode()).digest()
    signature = base64.b64encode(
        hmac.new(decoded, request.route.value.encode() + digest, hashlib.sha512).digest()
    ).decode()
    return body, signature


class KrakenExposureHTTPTransport:
    """Exact two-route transport. Construction does not perform network I/O."""

    __slots__ = ("_clock",)

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    def post(
        self,
        request: OfflineExposureRequest,
        credential: EphemeralKrakenCredential,
        nonce_provider: MonotonicNonceProvider,
    ) -> KrakenExposureHTTPObservation:
        _exact_parameters(request)
        if (
            request.credential_reference_identity != credential.reference.content_identity
            or nonce_provider.reference_identity != credential.reference.content_identity
        ):
            raise KrakenExposureTransportError("KRAKEN_EXPOSURE_REQUEST_INVALID")
        try:
            nonce = nonce_provider.next()
            body, signature = sign_exposure_request(request, nonce, credential.secret_buffer())
            api_key = credential.api_key_bytes().decode("ascii")
        except (UnicodeError, ValueError):
            raise KrakenExposureTransportError("KRAKEN_EXPOSURE_SIGNING_FAILURE") from None

        started_at = self._clock()
        connection: HTTPSConnection | None = None
        network_call_performed = False
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            connection = HTTPSConnection(
                KRAKEN_EXPOSURE_HOST,
                port=443,
                timeout=KRAKEN_EXPOSURE_TIMEOUT_SECONDS,
                context=context,
            )
            network_call_performed = True
            connection.request(
                "POST",
                request.route.value,
                body=body,
                headers={
                    "Accept": "application/json",
                    "API-Key": api_key,
                    "API-Sign": signature,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "ATP-Kraken-Exposure-Qualification/1",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise KrakenExposureTransportError(
                    "KRAKEN_EXPOSURE_HTTP_FAILURE", network_call_performed=True
                )
            encoding = response.getheader("Content-Encoding")
            if encoding not in (None, "", "identity"):
                raise KrakenExposureTransportError(
                    "KRAKEN_EXPOSURE_RESPONSE_INVALID", network_call_performed=True
                )
            raw = response.read(KRAKEN_EXPOSURE_MAX_RESPONSE_BYTES + 1)
            if len(raw) > KRAKEN_EXPOSURE_MAX_RESPONSE_BYTES:
                raise KrakenExposureTransportError(
                    "KRAKEN_EXPOSURE_RESPONSE_TOO_LARGE", network_call_performed=True
                )
            try:
                payload = json.loads(raw)
            except (UnicodeError, ValueError, RecursionError):
                raise KrakenExposureTransportError(
                    "KRAKEN_EXPOSURE_JSON_INVALID", network_call_performed=True
                ) from None
            return KrakenExposureHTTPObservation(
                request.route,
                request.content_identity,
                payload,
                started_at,
                self._clock(),
            )
        except KrakenExposureTransportError as exc:
            if exc.network_call_performed:
                raise
            raise KrakenExposureTransportError(str(exc), network_call_performed=True) from None
        except TimeoutError:
            raise KrakenExposureTransportError(
                "KRAKEN_EXPOSURE_TIMEOUT", network_call_performed=network_call_performed
            ) from None
        except (OSError, HTTPException, ssl.SSLError):
            raise KrakenExposureTransportError(
                "KRAKEN_EXPOSURE_NETWORK_FAILURE",
                network_call_performed=network_call_performed,
            ) from None
        finally:
            if connection is not None:
                connection.close()
            api_key = ""
            signature = ""
            body = ""
