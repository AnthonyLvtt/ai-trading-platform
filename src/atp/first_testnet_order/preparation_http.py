"""Closed GET-only Testnet source. No economic path, redirect, or retry."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlencode

from atp.exchange.read_only import EvidenceError, safe_json
from atp.testnet_activation.runtime_credentials import ReferencedEnvironmentCredentialsProvider

ROUTES = frozenset(
    {"time", "exchangeInfo", "klines", "trades", "avgPrice", "account", "openOrders"}
)


class TestnetReadOnlySource:
    resources = ROUTES

    def __init__(self, provider: ReferencedEnvironmentCredentialsProvider) -> None:
        self._provider = provider

    def read(self, resource: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
        if resource not in self.resources:
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        query = urlencode(parameters)
        headers = {}
        if resource in ("account", "openOrders", "order", "myTrades"):
            material = self._provider.load()
            if material is None:
                raise EvidenceError("CREDENTIAL_CAPABILITY_INVALID")
            now = datetime.now(UTC)
            elapsed = now - datetime(1970, 1, 1, tzinfo=UTC)
            millis = elapsed.days * 86400000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
            query = urlencode(parameters + (("timestamp", str(millis)), ("recvWindow", "5000")))
            query += (
                "&signature="
                + hmac.new(material.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            )
            headers["X-MBX-APIKEY"] = material.api_key
        return _get(resource, query, headers)

    def __repr__(self) -> str:
        return "TestnetReadOnlySource(<redacted>)"


PUBLIC_ROUTES = frozenset({"time", "exchangeInfo", "avgPrice"})


class PublicTestnetSource:
    """Unsigned public GETs only: it holds no credential provider and cannot read accounts."""

    def read(self, resource: str, parameters: tuple[tuple[str, str], ...] = ()) -> object:
        if resource not in PUBLIC_ROUTES:
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        return _get(resource, urlencode(parameters), {})

    def __repr__(self) -> str:
        return "PublicTestnetSource()"


def _get(resource: str, query: str, headers: dict[str, str]) -> object:
    connection = HTTPSConnection("testnet.binance.vision", timeout=10)
    try:
        connection.request(
            "GET", "/api/v3/" + resource + ("?" + query if query else ""), headers=headers
        )
        response = connection.getresponse()
        if response.status != 200:
            raise EvidenceError("READ_ONLY_SOURCE_UNAVAILABLE")
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise EvidenceError("READ_ONLY_SOURCE_UNAVAILABLE")
        result = json.loads(body)
        safe_json(result)
        return result
    except (OSError, HTTPException, UnicodeError, ValueError):
        raise EvidenceError("READ_ONLY_SOURCE_UNAVAILABLE") from None
    finally:
        connection.close()


class ReconciliationReadOnlySource(TestnetReadOnlySource):
    """Explicit signed GET-only lookup; no submit, retry, cancel or withdrawal route."""

    resources = frozenset({"order", "myTrades"})
