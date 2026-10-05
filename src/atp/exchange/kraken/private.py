"""Kraken Spot private read-only primitives.

There is no HTTP transport in this module.  Only offline request signing, nonce
generation, and strict parsing for the three approved observation routes exist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import urlencode

from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.private_contracts import (
    AccountBalanceEvidence,
    AccountOpenOrdersEvidence,
    CanonicalAssetId,
    CanonicalBalance,
    CanonicalOpenOrder,
    PrivateCredentialCapabilityEvidence,
    PrivateCredentialReference,
)
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class KrakenPrivateReadRoute(StrEnum):
    API_KEY_INFO = "/0/private/GetApiKeyInfo"
    BALANCE = "/0/private/Balance"
    OPEN_ORDERS = "/0/private/OpenOrders"


KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST = frozenset(route.value for route in KrakenPrivateReadRoute)
KRAKEN_REQUIRED_READ_PERMISSIONS = frozenset({"query-funds", "query-open-trades"})
KRAKEN_WRITE_PERMISSIONS = frozenset(
    {
        "add-funds",
        "withdraw-funds",
        "earn-funds",
        "modify-trades",
        "close-trades",
        "add-withdraw-address",
        "update-withdraw-address",
    }
)


class KrakenPrivateError(EvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class KrakenAccountWideOpenOrdersRequest(EvidenceRecord):
    """Exact semantic request whose identity proves an unfiltered account read."""

    credential_reference_identity: ContentIdentity
    route: KrakenPrivateReadRoute = KrakenPrivateReadRoute.OPEN_ORDERS
    parameters: tuple[tuple[str, str], ...] = ()
    scope: str = "ACCOUNT_WIDE"

    def __post_init__(self) -> None:
        if (
            type(self.credential_reference_identity) is not ContentIdentity
            or self.route is not KrakenPrivateReadRoute.OPEN_ORDERS
            or self.parameters != ()
            or self.scope != "ACCOUNT_WIDE"
        ):
            raise KrakenPrivateError("KRAKEN_OPEN_ORDERS_REQUEST_INVALID")
        EvidenceRecord.__post_init__(self)


def account_wide_open_orders_request(
    reference: PrivateCredentialReference,
) -> KrakenAccountWideOpenOrdersRequest:
    _bound(reference)
    return KrakenAccountWideOpenOrdersRequest(reference.content_identity)


_NONCE_LOCK = threading.Lock()
_NONCE_STATE: dict[ContentIdentity, tuple[int, int]] = {}


class MonotonicNonceProvider:
    """Process-local, per-reference nonce source with rollback detection."""

    __slots__ = ("_clock_ns", "reference_identity")

    def __init__(
        self,
        reference_identity: ContentIdentity,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(reference_identity) is not ContentIdentity:
            raise KrakenPrivateError("KRAKEN_NONCE_REFERENCE_INVALID")
        self.reference_identity = reference_identity
        self._clock_ns = clock_ns

    def next(self) -> int:
        with _NONCE_LOCK:
            raw = self._clock_ns()
            if type(raw) is not int or raw < 0:
                raise KrakenPrivateError("KRAKEN_NONCE_CLOCK_INVALID")
            wall_ms = raw // 1_000_000
            last_wall_ms, last_nonce = _NONCE_STATE.get(self.reference_identity, (-1, -1))
            if wall_ms < last_wall_ms:
                raise KrakenPrivateError("KRAKEN_NONCE_CLOCK_ROLLBACK")
            nonce = max(wall_ms, last_nonce + 1)
            if nonce >= 2**64:
                raise KrakenPrivateError("KRAKEN_NONCE_EXHAUSTED")
            _NONCE_STATE[self.reference_identity] = (wall_ms, nonce)
            return nonce


def sign_private_read_request(
    route: KrakenPrivateReadRoute,
    nonce: int,
    secret: str | bytes | bytearray,
    parameters: Sequence[tuple[str, str]] = (),
) -> tuple[str, str]:
    """Return form body and API-Sign for an allowlisted read route only."""
    if (
        type(route) is not KrakenPrivateReadRoute
        or route.value not in KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
    ):
        raise KrakenPrivateError("KRAKEN_PRIVATE_ROUTE_FORBIDDEN")
    if type(nonce) is not int or not 0 <= nonce < 2**64:
        raise KrakenPrivateError("KRAKEN_NONCE_INVALID")
    if parameters or any(
        type(key) is not str or type(value) is not str or not key or key in {"nonce", "otp"}
        for key, value in parameters
    ):
        raise KrakenPrivateError("KRAKEN_PRIVATE_PARAMETERS_INVALID")
    body = urlencode((("nonce", str(nonce)), *parameters))
    try:
        decoded = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError):
        raise KrakenPrivateError("KRAKEN_SECRET_INVALID") from None
    if not decoded:
        raise KrakenPrivateError("KRAKEN_SECRET_INVALID")
    digest = hashlib.sha256(f"{nonce}{body}".encode()).digest()
    signature = base64.b64encode(
        hmac.new(decoded, route.value.encode() + digest, hashlib.sha512).digest()
    ).decode()
    return body, signature


def _envelope(payload: object) -> object:
    if (
        type(payload) is not dict
        or set(payload) != {"error", "result"}
        or type(payload.get("error")) is not list
        or payload["error"]
    ):
        raise KrakenPrivateError("KRAKEN_PRIVATE_ENVELOPE_INVALID")
    return payload["result"]


def _positive_decimal(value: object, *, allow_zero: bool = True) -> Decimal:
    if type(value) is not str:
        raise KrakenPrivateError("KRAKEN_PRIVATE_DECIMAL_INVALID")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise KrakenPrivateError("KRAKEN_PRIVATE_DECIMAL_INVALID") from None
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise KrakenPrivateError("KRAKEN_PRIVATE_DECIMAL_INVALID")
    return result


def _bound(
    reference: PrivateCredentialReference,
    capability: PrivateCredentialCapabilityEvidence | None = None,
) -> None:
    if reference.venue is not VenueId.KRAKEN:
        raise KrakenPrivateError("KRAKEN_PRIVATE_REFERENCE_MISMATCH")
    if capability is not None and (
        capability.venue is not VenueId.KRAKEN
        or capability.credential_reference_identity != reference.content_identity
    ):
        raise KrakenPrivateError("KRAKEN_PRIVATE_CAPABILITY_MISMATCH")


def parse_api_key_info(
    payload: object,
    reference: PrivateCredentialReference,
    observed_at: datetime,
) -> PrivateCredentialCapabilityEvidence:
    """Sanitize API-key metadata down to the exact least-privilege capability."""
    _bound(reference)
    result = _envelope(payload)
    if type(result) is not dict or "permissions" not in result:
        raise KrakenPrivateError("KRAKEN_API_KEY_INFO_INVALID")
    permissions = result.get("permissions")
    if (
        type(permissions) is not list
        or any(type(item) is not str for item in permissions)
        or len(set(permissions)) != len(permissions)
    ):
        raise KrakenPrivateError("KRAKEN_PERMISSION_EVIDENCE_INVALID")
    permission_set = frozenset(permissions)
    if (
        permission_set != KRAKEN_REQUIRED_READ_PERMISSIONS
        or permission_set & KRAKEN_WRITE_PERMISSIONS
    ):
        raise KrakenPrivateError("KRAKEN_LEAST_PRIVILEGE_REQUIRED")
    if observed_at.tzinfo is None:
        raise KrakenPrivateError("KRAKEN_PRIVATE_TIME_INVALID")
    sanitized = {"permissions": sorted(permission_set), "venue": VenueId.KRAKEN.value}
    return PrivateCredentialCapabilityEvidence(
        venue=VenueId.KRAKEN,
        credential_reference_identity=reference.content_identity,
        permissions=tuple(sorted(permission_set)),
        funds_query=True,
        open_orders_query=True,
        trading_capability_absent=True,
        withdrawal_capability_absent=True,
        observed_at=observed_at,
        source_identity=ContentIdentity.from_canonical(sanitized),
    )


def parse_balances(
    payload: object,
    reference: PrivateCredentialReference,
    capability: PrivateCredentialCapabilityEvidence,
    observed_at: datetime,
) -> AccountBalanceEvidence:
    _bound(reference, capability)
    result = _envelope(payload)
    if type(result) is not dict or not set(result) <= {"XXBT", "ZEUR"}:
        raise KrakenPrivateError("KRAKEN_BALANCE_ASSET_UNKNOWN")
    native = {CanonicalAssetId.BTC: "XXBT", CanonicalAssetId.EUR: "ZEUR"}
    balances = tuple(
        CanonicalBalance(asset, _positive_decimal(result[native[asset]]))
        for asset in sorted(native, key=lambda item: item.value)
        if native[asset] in result
    )
    return AccountBalanceEvidence(
        venue=VenueId.KRAKEN,
        credential_reference_identity=reference.content_identity,
        capability_identity=capability.content_identity,
        balances=balances,
        scope="ACCOUNT_WIDE_DEFAULT_WALLET",
        observed_at=observed_at,
        source_identity=ContentIdentity.from_canonical(result),
    )


def _opened_at(value: object) -> datetime:
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise KrakenPrivateError("KRAKEN_OPEN_ORDER_TIME_INVALID")
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (OverflowError, OSError, ValueError):
        raise KrakenPrivateError("KRAKEN_OPEN_ORDER_TIME_INVALID") from None


def _parse_open_order(order_id: object, raw: object) -> CanonicalOpenOrder:
    expected = {
        "refid",
        "userref",
        "status",
        "opentm",
        "starttm",
        "expiretm",
        "descr",
        "vol",
        "vol_exec",
        "cost",
        "fee",
        "price",
        "stopprice",
        "limitprice",
        "misc",
        "oflags",
        "trades",
    }
    if type(order_id) is not str or type(raw) is not dict or set(raw) != expected:
        raise KrakenPrivateError("KRAKEN_OPEN_ORDER_INVALID")
    descr = raw.get("descr")
    descr_expected = {"pair", "type", "ordertype", "price", "price2", "leverage", "order", "close"}
    if (
        raw.get("status") != "open"
        or type(descr) is not dict
        or set(descr) != descr_expected
        or descr.get("pair") not in {"XBTEUR", "XBT/EUR", "XXBTZEUR"}
        or descr.get("type") not in {"buy", "sell"}
        or descr.get("ordertype") not in {"limit", "market"}
        or descr.get("leverage") not in {"none", "1:1"}
        or type(raw.get("trades")) is not list
    ):
        raise KrakenPrivateError("KRAKEN_OPEN_ORDER_UNSAFE")
    return CanonicalOpenOrder(
        exchange_order_id=order_id,
        instrument=BTC_EUR,
        side=str(descr["type"]).upper(),
        order_type=str(descr["ordertype"]).upper(),
        quantity=_positive_decimal(raw["vol"], allow_zero=False),
        executed_quantity=_positive_decimal(raw["vol_exec"]),
        opened_at=_opened_at(raw["opentm"]),
    )


def parse_open_orders(
    payload: object,
    reference: PrivateCredentialReference,
    capability: PrivateCredentialCapabilityEvidence,
    request: KrakenAccountWideOpenOrdersRequest,
    observed_at: datetime,
) -> AccountOpenOrdersEvidence:
    _bound(reference, capability)
    if (
        type(request) is not KrakenAccountWideOpenOrdersRequest
        or request.credential_reference_identity != reference.content_identity
        or request.route is not KrakenPrivateReadRoute.OPEN_ORDERS
        or request.parameters != ()
        or request.scope != "ACCOUNT_WIDE"
    ):
        raise KrakenPrivateError("KRAKEN_OPEN_ORDERS_REQUEST_MISMATCH")
    result = _envelope(payload)
    if type(result) is not dict or set(result) != {"open"} or type(result.get("open")) is not dict:
        raise KrakenPrivateError("KRAKEN_OPEN_ORDERS_INCOMPLETE")
    raw_orders: Mapping[object, object] = result["open"]
    orders = tuple(
        _parse_open_order(order_id, raw_orders[order_id])
        for order_id in sorted(raw_orders, key=str)
    )
    return AccountOpenOrdersEvidence(
        venue=VenueId.KRAKEN,
        credential_reference_identity=reference.content_identity,
        capability_identity=capability.content_identity,
        request_identity=request.content_identity,
        orders=orders,
        scope="ACCOUNT_WIDE",
        complete=True,
        observed_at=observed_at,
        source_identity=ContentIdentity.from_canonical(result),
    )
