"""Closed Binance Testnet HTTP transport. No redirects, proxies or POST retries."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlencode

from atp.exchange.model import ExchangeOrderRequest, Reason, decimal_text, valid
from atp.ops.inspection import validate_readiness_result
from atp.ops.model import OperationalEnvironment, OperationalReadinessResult, ReadinessStatus


class Operation(StrEnum):
    SUBMIT = "SUBMIT"
    QUERY = "QUERY"
    PING = "PING"


@dataclass(frozen=True, slots=True, repr=False)
class TransportReply:
    http_status: int | None = None
    body: object = None
    error: Reason | None = None
    possibly_sent: bool = False


class ExchangeTransport(Protocol):
    def perform(
        self,
        operation: Operation,
        order: ExchangeOrderRequest | None,
        at: datetime,
        readiness: object,
    ) -> TransportReply: ...


class CredentialMaterial:
    __slots__ = ("api_key", "api_secret")

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret

    def __repr__(self) -> str:
        return "CredentialMaterial(<redacted>)"


class ExchangeCredentialsProvider(Protocol):
    def load(self) -> CredentialMaterial | None: ...


class EnvironmentCredentialsProvider:
    """Read credentials only at dispatch; never enumerate or retain the environment."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ

    def load(self) -> CredentialMaterial | None:
        source = os.environ if self._environ is None else self._environ
        key = source.get("ATP_BINANCE_TESTNET_API_KEY")
        secret = source.get("ATP_BINANCE_TESTNET_API_SECRET")
        if not key or not secret:
            return None
        return CredentialMaterial(key, secret)

    def __repr__(self) -> str:
        return "EnvironmentCredentialsProvider(<redacted>)"


def runtime_ready(readiness: object) -> bool:
    return (
        validate_readiness_result(readiness)
        and isinstance(readiness, OperationalReadinessResult)
        and readiness.environment is OperationalEnvironment.TESTNET
        and readiness.readiness_status is ReadinessStatus.READY
    )


def wire_parameters(
    operation: Operation, order: ExchangeOrderRequest, at: datetime
) -> dict[str, str]:
    assert order is not None
    params = {
        "symbol": order.symbol,
        "timestamp": str(int(at.timestamp() * 1000)),
        "recvWindow": "5000",
    }
    if operation is Operation.SUBMIT:
        params.update(
            side=order.side.value,
            type="MARKET",
            quantity=decimal_text(order.quantity),
            newClientOrderId=order.client_order_id,
            newOrderRespType="RESULT",
        )
    else:
        params["origClientOrderId"] = order.client_order_id
    return params


class BinanceTestnetHTTPTransport:
    def __init__(self, credentials: ExchangeCredentialsProvider) -> None:
        self._credentials = credentials

    def perform(
        self,
        operation: Operation,
        order: ExchangeOrderRequest | None,
        at: datetime,
        readiness: object,
    ) -> TransportReply:
        # Defense in depth: even a test harness cannot activate this HTTP transport.
        if not runtime_ready(readiness):
            return TransportReply(error=Reason.TESTNET_NOT_AUTHORIZED)
        if (
            type(operation) is not Operation
            or (operation is not Operation.PING and not valid(order, ExchangeOrderRequest))
            or not valid(at, datetime)
            or (order is not None and order.environment != "TESTNET")
        ):
            return TransportReply(error=Reason.INVALID_EXCHANGE_ORDER)
        material = self._credentials.load()
        if material is None:
            return TransportReply(error=Reason.CREDENTIALS_UNAVAILABLE)
        if type(material) is not CredentialMaterial or any(
            type(v) is not str or not v for v in (material.api_key, material.api_secret)
        ):
            return TransportReply(error=Reason.INVALID_CREDENTIALS)
        return self._dispatch(operation, order, at, material)

    def _dispatch(
        self,
        operation: Operation,
        order: ExchangeOrderRequest | None,
        at: datetime,
        material: CredentialMaterial,
    ) -> TransportReply:
        # Fixed host and paths; http.client neither follows redirects nor consumes proxy env vars.
        connection = http.client.HTTPSConnection(
            "testnet.binance.vision", timeout=10, context=ssl.create_default_context()
        )
        possible = False
        try:
            connection.connect()
            if operation is Operation.PING:
                path, body, headers = "/api/v3/ping", None, {}
                method = "GET"
            else:
                assert order is not None
                query = urlencode(wire_parameters(operation, order, at))
                signature = hmac.new(
                    material.api_secret.encode(), query.encode(), hashlib.sha256
                ).hexdigest()
                signed = query + "&signature=" + signature
                headers = {
                    "X-MBX-APIKEY": material.api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                method = "POST" if operation is Operation.SUBMIT else "GET"
                path = "/api/v3/order" if method == "POST" else "/api/v3/order?" + signed
                body = signed if method == "POST" else None
            possible = True
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                return TransportReply(error=Reason.MALFORMED_EXCHANGE_RESPONSE, possibly_sent=True)
            try:
                decoded = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                return TransportReply(error=Reason.MALFORMED_EXCHANGE_RESPONSE, possibly_sent=True)
            return TransportReply(response.status, decoded, possibly_sent=True)
        except TimeoutError:
            return TransportReply(error=Reason.TRANSPORT_TIMEOUT, possibly_sent=possible)
        except (OSError, http.client.HTTPException):
            return TransportReply(error=Reason.TRANSPORT_UNAVAILABLE, possibly_sent=possible)
        finally:
            connection.close()
