"""Read-only Exchange evidence contracts. No transport, credentials or execution authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction

from atp.exchange.model import canonical, typed
from atp.observability.events import SENSITIVE_KEYS
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes


class EvidenceError(ValueError):
    pass


def safe_json(value: object, depth: int = 0) -> None:
    if depth > 25:
        raise EvidenceError("INVALID_INPUT")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key.casefold() in SENSITIVE_KEYS:
                raise EvidenceError("CREDENTIAL_MATERIAL_DETECTED")
            safe_json(item, depth + 1)
    elif type(value) is list:
        for item in value:
            safe_json(item, depth + 1)
    elif type(value) is str:
        if re.search(
            r"(?i)(?:" + "|".join(re.escape(k) for k in SENSITIVE_KEYS) + r')[\s"\x27]*[:=]', value
        ):
            raise EvidenceError("CREDENTIAL_MATERIAL_DETECTED")
    elif value is not None and type(value) not in (bool, int):
        raise EvidenceError("INVALID_INPUT")


def encoded(value: object) -> object:
    if type(value) is tuple:
        return [encoded(v) for v in value]
    if type(value) is bytes:
        data = json.loads(value)
        safe_json(data)
        return data
    if is_dataclass(value) and not isinstance(value, type) and type(value) is not ContentIdentity:
        return {
            f.name: encoded(getattr(value, f.name))
            for f in fields(value)
            if f.name != "content_identity"
        }
    return canonical(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRecord:
    content_identity: ContentIdentity = field(init=False)

    def __post_init__(self) -> None:
        for flag in ("submission_authorized", "side_effect_performed", "safe_to_retry"):
            if hasattr(self, flag) and getattr(self, flag) is not False:
                raise EvidenceError("SIDE_EFFECT_NOT_AUTHORIZED")
        value = encoded(self)
        safe_json(value)
        digest = ContentIdentity.from_canonical(value)
        if hasattr(self, "content_identity") and self.content_identity != digest:
            raise EvidenceError("IDENTITY_MISMATCH")
        object.__setattr__(self, "content_identity", digest)


def verify_record(value: object, expected: type[object]) -> bool:
    try:
        if not typed(value, expected):
            return False

        def visit(item: object) -> None:
            if type(item) is tuple:
                for child in item:
                    visit(child)
            elif is_dataclass(item) and not isinstance(item, type):
                for f in fields(item):
                    visit(getattr(item, f.name))
                post = getattr(item, "__post_init__", None)
                if post is not None:
                    post()

        visit(value)
        safe_json(encoded(value))
        return True
    except (AttributeError, TypeError, ValueError, ValidationError, RecursionError):
        return False


def decimal_field(value: object) -> Decimal:
    if type(value) is not str:
        raise EvidenceError("INVALID_DECIMAL")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise EvidenceError("INVALID_DECIMAL") from None
    if not result.is_finite() or result < 0:
        raise EvidenceError("INVALID_DECIMAL")
    return result


def timestamp(value: object) -> datetime:
    if type(value) is not int or value < 0:
        raise EvidenceError("INVALID_TIME")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)
    except OverflowError:
        raise EvidenceError("INVALID_TIME") from None


class ExchangeOrderStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    PENDING_CANCEL = "PENDING_CANCEL"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReconciliationLookup(EvidenceRecord):
    symbol: str
    client_order_id: str | None = None
    exchange_order_id: int | None = None
    environment: str = "TESTNET"

    def parameters(self) -> tuple[tuple[str, str], ...]:
        if (
            not verify_record(self, ReconciliationLookup)
            or self.environment != "TESTNET"
            or not re.fullmatch(r"[A-Z0-9]{2,30}", self.symbol)
            or (self.client_order_id is None) == (self.exchange_order_id is None)
            or (
                self.client_order_id is not None
                and not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", self.client_order_id)
            )
            or (self.exchange_order_id is not None and self.exchange_order_id < 0)
        ):
            raise EvidenceError("INVALID_LOOKUP")
        key, value = (
            ("origClientOrderId", self.client_order_id)
            if self.client_order_id is not None
            else ("orderId", str(self.exchange_order_id))
        )
        return (("symbol", self.symbol), (key, value))


@dataclass(frozen=True, slots=True)
class ExchangeFill(EvidenceRecord):
    exchange_order_id: int
    client_order_id: str
    trade_id: int
    symbol: str
    price: Decimal
    quantity: Decimal
    quote_quantity: Decimal | None
    execution_time: datetime


@dataclass(frozen=True, slots=True)
class ExchangeOrderSnapshot(EvidenceRecord):
    exchange_order_id: int
    client_order_id: str
    symbol: str
    status: ExchangeOrderStatus
    side: str
    order_type: str
    quantity: Decimal
    executed_quantity: Decimal
    effective_at: datetime
    fills: tuple[ExchangeFill, ...]
    source_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class ReconciliationResult(EvidenceRecord):
    status: str
    reason_code: str
    snapshot: ExchangeOrderSnapshot | None = None
    safe_to_retry: bool = False
    side_effect_performed: bool = False


def parse_reconciliation(lookup: object, order: object, trades: object) -> ReconciliationResult:
    """Inspect injected GET order/account-trades payloads; never sends a lookup."""
    try:
        if not verify_record(lookup, ReconciliationLookup):
            raise EvidenceError("INVALID_LOOKUP")
        assert isinstance(lookup, ReconciliationLookup)
        lookup.parameters()
        safe_json(order)
        safe_json(trades)
        if type(order) is not dict or type(trades) is not list:
            raise EvidenceError("MALFORMED_RESPONSE")
        oid, cid, symbol = order.get("orderId"), order.get("clientOrderId"), order.get("symbol")
        if (
            type(oid) is not int
            or oid < 0
            or type(cid) is not str
            or not cid
            or symbol != lookup.symbol
            or (lookup.exchange_order_id is not None and oid != lookup.exchange_order_id)
            or (lookup.client_order_id is not None and cid != lookup.client_order_id)
        ):
            raise EvidenceError("LOOKUP_MISMATCH")
        raw_status = order.get("status")
        if type(raw_status) is not str or not raw_status:
            raise EvidenceError("MALFORMED_RESPONSE")
        status = (
            ExchangeOrderStatus(raw_status)
            if raw_status in ExchangeOrderStatus._value2member_map_
            else ExchangeOrderStatus.UNKNOWN
        )
        qty, executed = decimal_field(order.get("origQty")), decimal_field(order.get("executedQty"))
        if (
            qty <= 0
            or executed > qty
            or order.get("side") not in ("BUY", "SELL")
            or order.get("type") != "MARKET"
        ):
            raise EvidenceError("MALFORMED_RESPONSE")
        at = timestamp(order.get("updateTime"))
        fills = []
        seen = set()
        for trade in trades:
            if type(trade) is not dict:
                raise EvidenceError("MALFORMED_RESPONSE")
            tid = trade.get("id")
            if (
                type(tid) is not int
                or tid < 0
                or tid in seen
                or trade.get("orderId") != oid
                or type(trade.get("orderId")) is not int
                or trade.get("symbol") != symbol
            ):
                raise EvidenceError("MALFORMED_RESPONSE")
            seen.add(tid)
            price, quantity = decimal_field(trade.get("price")), decimal_field(trade.get("qty"))
            when = timestamp(trade.get("time"))
            if price <= 0 or quantity <= 0 or when > at:
                raise EvidenceError("MALFORMED_RESPONSE")
            fills.append(
                ExchangeFill(
                    oid,
                    cid,
                    tid,
                    symbol,
                    price,
                    quantity,
                    decimal_field(trade["quoteQty"]) if "quoteQty" in trade else None,
                    when,
                )
            )
        if sum((Fraction(f.quantity) for f in fills), Fraction(0)) > Fraction(executed):
            raise EvidenceError("MALFORMED_RESPONSE")
        snapshot = ExchangeOrderSnapshot(
            oid,
            cid,
            symbol,
            status,
            order["side"],
            order["type"],
            qty,
            executed,
            at,
            tuple(sorted(fills, key=lambda f: (f.execution_time, f.trade_id))),
            ContentIdentity.from_bytes(canonical_json_bytes({"order": order, "trades": trades})),
        )
        return ReconciliationResult(
            "BLOCKED" if status is ExchangeOrderStatus.UNKNOWN else "PASSED",
            "AMBIGUOUS_SUBMISSION_STATE"
            if status is ExchangeOrderStatus.UNKNOWN
            else "RECONCILIATION_ESTABLISHED",
            snapshot,
        )
    except (EvidenceError, KeyError, TypeError, ValueError, ValidationError):
        return ReconciliationResult("BLOCKED", "RESPONSE_MAPPING_INVALID")


@dataclass(frozen=True, slots=True)
class ReadOnlyQuery(EvidenceRecord):
    path: str
    parameters: tuple[tuple[str, str], ...]
    method: str = "GET"
    venue: str = "BINANCE_SPOT_TESTNET"
    submission_authorized: bool = False


def reconciliation_order_query(lookup: object) -> ReadOnlyQuery | None:
    if not verify_record(lookup, ReconciliationLookup):
        return None
    assert isinstance(lookup, ReconciliationLookup)
    try:
        return ReadOnlyQuery("/api/v3/order", lookup.parameters())
    except EvidenceError:
        return None


def reconciliation_fills_query(lookup: object) -> ReadOnlyQuery | None:
    if not verify_record(lookup, ReconciliationLookup):
        return None
    assert isinstance(lookup, ReconciliationLookup)
    # Trades require the established order ID: resolve a client lookup first.
    if lookup.exchange_order_id is None:
        return None
    try:
        return ReadOnlyQuery("/api/v3/myTrades", lookup.parameters())
    except EvidenceError:
        return None
