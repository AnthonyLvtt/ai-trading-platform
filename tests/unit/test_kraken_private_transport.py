from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

import atp.exchange.kraken.private_transport as private_transport_module
from atp.exchange.kraken.private import (
    KrakenPrivateReadRoute,
    MonotonicNonceProvider,
    account_wide_open_orders_request,
)
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.exchange.kraken.private_transport import (
    KRAKEN_PRIVATE_HOST,
    KRAKEN_PRIVATE_MAX_RESPONSE_BYTES,
    KRAKEN_PRIVATE_TIMEOUT_SECONDS,
    KrakenPrivateHTTPTransport,
    KrakenPrivateTransportError,
    api_key_info_request,
    balance_request,
)

AT = datetime(2026, 9, 29, 12, tzinfo=UTC)


def credential() -> EphemeralKrakenCredential:
    return EphemeralKrakenCredential(bytearray(b"public-key"), bytearray(b"c2VjcmV0"))


class Response:
    status = 200

    def __init__(self, body: bytes = b'{"error":[],"result":{}}') -> None:
        self.body = body

    def getheader(self, name: str):
        del name
        return None

    def read(self, size: int) -> bytes:
        assert size == KRAKEN_PRIVATE_MAX_RESPONSE_BYTES + 1
        return self.body


class Connection:
    instances: list[Connection] = []
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


def test_transport_has_fixed_host_post_routes_and_nonce_only_body(monkeypatch) -> None:
    monkeypatch.setattr(private_transport_module, "HTTPSConnection", Connection)
    cred = credential()
    provider = MonotonicNonceProvider(
        cred.reference.content_identity, clock_ns=lambda: 1_234_000_000
    )
    transport = KrakenPrivateHTTPTransport(clock=lambda: AT)
    requests = (
        api_key_info_request(cred),
        balance_request(cred),
        account_wide_open_orders_request(cred.reference),
    )
    for request in requests:
        observation = transport.post(request, cred, provider)
        assert observation.route is request.route
    assert len(Connection.instances) == 3
    for connection, request in zip(Connection.instances, requests, strict=True):
        assert (connection.host, connection.port, connection.timeout) == (
            KRAKEN_PRIVATE_HOST,
            443,
            KRAKEN_PRIVATE_TIMEOUT_SECONDS,
        )
        assert connection.closed is True
        method, path, body, headers = connection.requests[0]
        assert (method, path) == ("POST", request.route.value)
        assert body.startswith("nonce=") and "&" not in body
        assert set(headers) == {
            "Accept",
            "API-Key",
            "API-Sign",
            "Content-Type",
            "User-Agent",
        }
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    cred.close()


def test_transport_rejects_request_mismatch_before_network(monkeypatch) -> None:
    monkeypatch.setattr(private_transport_module, "HTTPSConnection", Connection)
    first = credential()
    second = EphemeralKrakenCredential(bytearray(b"other-key"), bytearray(b"c2VjcmV0"))
    provider = MonotonicNonceProvider(first.reference.content_identity)
    with pytest.raises(
        KrakenPrivateTransportError, match="KRAKEN_PRIVATE_REQUEST_INVALID"
    ) as error:
        KrakenPrivateHTTPTransport().post(balance_request(second), first, provider)
    assert error.value.network_call_performed is False
    assert Connection.instances == []
    first.close()
    second.close()


def test_nonce_failure_is_sanitized_before_network(monkeypatch) -> None:
    monkeypatch.setattr(private_transport_module, "HTTPSConnection", Connection)
    cred = EphemeralKrakenCredential(bytearray(b"rollback-key"), bytearray(b"c2VjcmV0"))
    provider = MonotonicNonceProvider(cred.reference.content_identity, clock_ns=lambda: -1)
    with pytest.raises(
        KrakenPrivateTransportError, match="KRAKEN_NONCE_OR_SIGNING_FAILURE"
    ) as error:
        KrakenPrivateHTTPTransport().post(balance_request(cred), cred, provider)
    assert error.value.network_call_performed is False
    assert Connection.instances == []
    cred.close()


def test_closed_requests_cannot_change_route_or_parameters() -> None:
    cred = credential()
    with pytest.raises(KrakenPrivateTransportError, match="KRAKEN_PRIVATE_REQUEST_INVALID"):
        replace(api_key_info_request(cred), route=KrakenPrivateReadRoute.BALANCE)
    with pytest.raises(KrakenPrivateTransportError, match="KRAKEN_PRIVATE_REQUEST_INVALID"):
        replace(balance_request(cred), parameters=(("asset", "XXBT"),))
    cred.close()


def test_oversized_and_malformed_responses_fail_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(private_transport_module, "HTTPSConnection", Connection)
    cred = credential()
    provider = MonotonicNonceProvider(cred.reference.content_identity)
    transport = KrakenPrivateHTTPTransport(clock=lambda: AT)
    for body, reason in (
        (b"x" * (KRAKEN_PRIVATE_MAX_RESPONSE_BYTES + 1), "KRAKEN_PRIVATE_RESPONSE_TOO_LARGE"),
        (b"not-json", "KRAKEN_PRIVATE_JSON_INVALID"),
    ):
        Connection.response = Response(body)
        with pytest.raises(KrakenPrivateTransportError, match=reason):
            transport.post(balance_request(cred), cred, provider)
    cred.close()


def test_response_payload_is_internal_and_not_serialized() -> None:
    body = json.dumps({"error": [], "result": {"apiKey": "sensitive"}}).encode()
    assert b"sensitive" in body
