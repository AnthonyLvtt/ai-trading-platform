from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

import atp.exchange.kraken.exposure_transport as module
from atp.exchange.kraken.exposure_transport import (
    KRAKEN_EXPOSURE_HOST,
    KRAKEN_EXPOSURE_MAX_RESPONSE_BYTES,
    KRAKEN_EXPOSURE_ROUTE_ALLOWLIST,
    KRAKEN_EXPOSURE_TIMEOUT_SECONDS,
    KrakenExposureHTTPTransport,
    KrakenExposureTransportError,
    sign_exposure_request,
)
from atp.exchange.kraken.private import KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST, MonotonicNonceProvider
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.kraken_private_qualification.exposure_observation import exact_offline_exposure_requests
from atp.shared.identity import ContentIdentity
from tests.unit.test_kraken_private import evidence

AT = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)


def credential() -> EphemeralKrakenCredential:
    return EphemeralKrakenCredential(bytearray(b"public-key"), bytearray(b"c2VjcmV0"))


def requests():
    ref, capability, *_ = evidence()
    cred = credential()
    object.__setattr__(cred, "reference", ref)
    account = ContentIdentity.from_text("account")
    return cred, exact_offline_exposure_requests(ref, capability, account)


class Response:
    status = 200

    def __init__(self, body: bytes = b'{"error":[],"result":{}}') -> None:
        self.body = body

    def getheader(self, name: str):
        del name
        return None

    def read(self, size: int) -> bytes:
        assert size == KRAKEN_EXPOSURE_MAX_RESPONSE_BYTES + 1
        return self.body


class Connection:
    instances: list["Connection"] = []
    response = Response()

    def __init__(self, host, *, port, timeout, context) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.requests = []
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, method, path, *, body, headers) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.__class__.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_connection() -> None:
    Connection.instances.clear()
    Connection.response = Response()


def test_exposure_allowlist_is_separate_and_non_economic() -> None:
    assert KRAKEN_EXPOSURE_ROUTE_ALLOWLIST == {
        "/0/private/BalanceEx",
        "/0/private/TradeVolume",
    }
    assert not KRAKEN_EXPOSURE_ROUTE_ALLOWLIST & KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
    assert not any(
        token in route.casefold()
        for route in KRAKEN_EXPOSURE_ROUTE_ALLOWLIST
        for token in ("addorder", "cancel", "withdraw", "deposit")
    )


def test_signer_accepts_only_exact_route_parameters() -> None:
    cred, reqs = requests()
    body0, _ = sign_exposure_request(reqs[0], 123, "c2VjcmV0")
    body1, _ = sign_exposure_request(reqs[1], 124, "c2VjcmV0")
    assert body0 == "nonce=123"
    assert body1 == "nonce=124&pair=XXBTZEUR"
    with pytest.raises(KrakenExposureTransportError, match="KRAKEN_EXPOSURE_REQUEST_INVALID"):
        sign_exposure_request(replace(reqs[1], parameters=()), 1, "c2VjcmV0")
    cred.close()


def test_transport_is_fixed_host_post_and_exact_two_routes(monkeypatch) -> None:
    monkeypatch.setattr(module, "HTTPSConnection", Connection)
    cred, reqs = requests()
    provider = MonotonicNonceProvider(
        cred.reference.content_identity, clock_ns=lambda: 1_234_000_000
    )
    transport = KrakenExposureHTTPTransport(clock=lambda: AT)
    for request in reqs:
        observation = transport.post(request, cred, provider)
        assert observation.route is request.route
        assert observation.request_identity == request.content_identity
    assert len(Connection.instances) == 2
    for connection, request in zip(Connection.instances, reqs, strict=True):
        assert (connection.host, connection.port, connection.timeout) == (
            KRAKEN_EXPOSURE_HOST,
            443,
            KRAKEN_EXPOSURE_TIMEOUT_SECONDS,
        )
        method, path, body, headers = connection.requests[0]
        assert (method, path) == ("POST", request.route.value)
        assert body.startswith("nonce=")
        if request.route.value.endswith("TradeVolume"):
            assert body.endswith("&pair=XXBTZEUR")
        else:
            assert "&" not in body
        assert set(headers) == {
            "Accept",
            "API-Key",
            "API-Sign",
            "Content-Type",
            "User-Agent",
        }
        assert connection.closed is True
    cred.close()


def test_mismatch_blocks_before_network(monkeypatch) -> None:
    monkeypatch.setattr(module, "HTTPSConnection", Connection)
    cred, reqs = requests()
    foreign = credential()
    provider = MonotonicNonceProvider(foreign.reference.content_identity)
    with pytest.raises(KrakenExposureTransportError, match="KRAKEN_EXPOSURE_REQUEST_INVALID") as err:
        KrakenExposureHTTPTransport().post(reqs[0], cred, provider)
    assert err.value.network_call_performed is False
    assert Connection.instances == []
    cred.close()
    foreign.close()
