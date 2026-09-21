"""CTO MARKET filter policy over explicit offline Exchange evidence. No rounding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from atp.exchange.read_only import (
    EvidenceError,
    EvidenceRecord,
    decimal_field,
    safe_json,
    verify_record,
)
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class SymbolFilterEvidence(EvidenceRecord):
    payload: bytes
    observed_at: datetime
    environment: str = "TESTNET"

    @property
    def source_identity(self) -> ContentIdentity:
        return ContentIdentity.from_bytes(self.payload)


@dataclass(frozen=True, slots=True)
class NotionalPriceEvidence(EvidenceRecord):
    symbol: str
    price: Decimal
    source_type: str
    source_identity: ContentIdentity
    observed_at: datetime
    effective_at: datetime
    avg_price_minutes: int


def parse_symbol_filters(payload: object, observed_at: object) -> SymbolFilterEvidence | None:
    try:
        safe_json(payload)
        if type(observed_at) is not datetime or observed_at.tzinfo is not UTC:
            return None
        evidence = SymbolFilterEvidence(canonical_json_bytes(payload), observed_at)
        _filters(evidence)
        return evidence
    except (ValueError, TypeError):
        return None


def _filters(evidence: SymbolFilterEvidence) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not verify_record(evidence, SymbolFilterEvidence) or evidence.environment != "TESTNET":
        raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
    data = json.loads(evidence.payload)
    if (
        type(data) is not dict
        or type(data.get("symbol")) is not str
        or not re.fullmatch(r"[A-Z0-9]{2,30}", data["symbol"])
        or type(data.get("status")) is not str
        or type(data.get("orderTypes")) is not list
        or not all(type(v) is str for v in data["orderTypes"])
        or len(set(data["orderTypes"])) != len(data["orderTypes"])
        or type(data.get("filters")) is not list
    ):
        raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
    result = {}
    schemas = {
        "PRICE_FILTER": ("minPrice", "maxPrice", "tickSize"),
        "LOT_SIZE": ("minQty", "maxQty", "stepSize"),
        "MARKET_LOT_SIZE": ("minQty", "maxQty", "stepSize"),
        "MIN_NOTIONAL": ("minNotional",),
        "NOTIONAL": ("minNotional", "maxNotional"),
    }
    integer_schemas = {
        "ICEBERG_PARTS": ("limit",),
        "TRAILING_DELTA": (
            "minTrailingAboveDelta",
            "maxTrailingAboveDelta",
            "minTrailingBelowDelta",
            "maxTrailingBelowDelta",
        ),
        "MAX_NUM_ORDERS": ("maxNumOrders",),
        "MAX_NUM_ORDER_LISTS": ("maxNumOrderLists",),
        "MAX_NUM_ALGO_ORDERS": ("maxNumAlgoOrders",),
        "MAX_NUM_ORDER_AMENDS": ("maxNumOrderAmends",),
    }
    schemas["PERCENT_PRICE_BY_SIDE"] = (
        "bidMultiplierUp",
        "bidMultiplierDown",
        "askMultiplierUp",
        "askMultiplierDown",
    )
    for item in data["filters"]:
        if type(item) is not dict or type(item.get("filterType")) is not str:
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        kind = item["filterType"]
        if kind not in schemas and kind not in integer_schemas:
            raise EvidenceError("UNSUPPORTED_SYMBOL_FILTER")
        if kind in result:
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        if kind in integer_schemas:
            if any(type(item.get(k)) is not int or item[k] < 0 for k in integer_schemas[kind]):
                raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
            if kind == "TRAILING_DELTA" and (
                item["minTrailingAboveDelta"] > item["maxTrailingAboveDelta"]
                or item["minTrailingBelowDelta"] > item["maxTrailingBelowDelta"]
            ):
                raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
            result[kind] = item
            continue
        if kind == "PERCENT_PRICE_BY_SIDE" and (
            type(item.get("avgPriceMins")) is not int or item["avgPriceMins"] < 0
        ):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        for key in schemas[kind]:
            decimal_field(item.get(key))
        bounds = schemas[kind]
        if kind != "PERCENT_PRICE_BY_SIDE" and len(bounds) >= 2:
            low, high = decimal_field(item[bounds[0]]), decimal_field(item[bounds[1]])
            if high > 0 and low > high:
                raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        if kind in ("MIN_NOTIONAL", "NOTIONAL"):
            flags = (
                ("applyToMarket",)
                if kind == "MIN_NOTIONAL"
                else ("applyMinToMarket", "applyMaxToMarket")
            )
            if (
                any(type(item.get(k)) is not bool for k in flags)
                or type(item.get("avgPriceMins")) is not int
                or item["avgPriceMins"] < 0
            ):
                raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        result[kind] = item
    return data, result


def check_market_filters(
    evidence: object,
    symbol: str,
    quantity: object,
    at: object,
    price: object = None,
    open_orders: object = None,
) -> str | None:
    return _check_market(evidence, symbol, quantity, at, price, open_orders, capacity=True)


def check_market_quantity(
    evidence: object,
    symbol: str,
    quantity: object,
    at: object,
    price: object = None,
) -> str | None:
    """Every MARKET filter except live order capacity, for offline feasibility only.

    Capacity needs a real open-orders read and is never assumed; the final gate still
    applies ``check_market_filters`` with complete, fresh open-orders evidence.
    """
    return _check_market(evidence, symbol, quantity, at, price, None, capacity=False)


def _check_market(
    evidence: object,
    symbol: str,
    quantity: object,
    at: object,
    price: object,
    open_orders: object,
    *,
    capacity: bool,
) -> str | None:
    try:
        if (
            not verify_record(evidence, SymbolFilterEvidence)
            or type(quantity) is not Decimal
            or not quantity.is_finite()
            or quantity <= 0
            or type(at) is not datetime
            or at.tzinfo is not UTC
        ):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        assert isinstance(evidence, SymbolFilterEvidence)
        data, filters = _filters(evidence)
        if (
            data["symbol"] != symbol
            or data["status"] != "TRADING"
            or "MARKET" not in data["orderTypes"]
            or evidence.observed_at > at
        ):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        if capacity:
            capacity_error = check_order_capacity(evidence, open_orders, symbol, at)
            if capacity_error is not None:
                raise EvidenceError(capacity_error)
        lot = market_quantity_rules(evidence)
        if lot is None:
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        low, high, step = (
            Fraction(decimal_field(lot[k])) for k in ("minQty", "maxQty", "stepSize")
        )
        q = Fraction(quantity)
        if (low > 0 and q < low) or (high > 0 and q > high) or (step > 0 and q % step != 0):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        n = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL"))
        if n is None:
            return None
        minimum = n["applyMinToMarket"] if n["filterType"] == "NOTIONAL" else n["applyToMarket"]
        maximum = n["applyMaxToMarket"] if n["filterType"] == "NOTIONAL" else False
        if not minimum and not maximum:
            return None
        if not verify_record(price, NotionalPriceEvidence):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        assert isinstance(price, NotionalPriceEvidence)
        window = n["avgPriceMins"]
        expected = "EXCHANGE_AVERAGE_PRICE" if window > 0 else "EXCHANGE_LAST_PRICE"
        if (
            price.symbol != symbol
            or price.price <= 0
            or price.source_type != expected
            or price.avg_price_minutes != window
            or not price.effective_at <= price.observed_at <= at
        ):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        notional = q * Fraction(price.price)
        if (minimum and notional < Fraction(decimal_field(n["minNotional"]))) or (
            maximum and notional > Fraction(decimal_field(n["maxNotional"]))
        ):
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        return None
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # Unknown filters retain their dedicated closed reason; never expose payload values.
        return (
            str(error.args[0])
            if isinstance(error, EvidenceError)
            and error.args
            in (
                ("UNSUPPORTED_SYMBOL_FILTER",),
                ("OPEN_ORDERS_EVIDENCE_STALE",),
                ("OPEN_ORDERS_EVIDENCE_INVALID",),
                ("ORDER_CAPACITY_EXCEEDED",),
            )
            else "SYMBOL_FILTER_INCOMPATIBLE"
        )


def market_notional_bounds(evidence: object) -> tuple[Fraction | None, Fraction | None]:
    """Exact MARKET-applicable (minimum, maximum) notional; ``None`` means not applicable."""
    if not verify_record(evidence, SymbolFilterEvidence):
        raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
    assert isinstance(evidence, SymbolFilterEvidence)
    _, filters = _filters(evidence)
    n = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL"))
    if n is None:
        return None, None
    minimum = n["applyMinToMarket"] if n["filterType"] == "NOTIONAL" else n["applyToMarket"]
    maximum = n["applyMaxToMarket"] if n["filterType"] == "NOTIONAL" else False
    return (
        Fraction(decimal_field(n["minNotional"])) if minimum else None,
        Fraction(decimal_field(n["maxNotional"])) if maximum else None,
    )


def market_notional_price_contract(evidence: object) -> tuple[str, int] | None:
    """Public inspection of the applicable MARKET price source; no price invention."""
    if not verify_record(evidence, SymbolFilterEvidence):
        return None
    assert isinstance(evidence, SymbolFilterEvidence)
    try:
        _, filters = _filters(evidence)
        n = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL"))
        if n is not None:
            applied = (
                n["applyMinToMarket"] or n["applyMaxToMarket"]
                if n["filterType"] == "NOTIONAL"
                else n["applyToMarket"]
            )
            if applied:
                window = n["avgPriceMins"]
                return ("EXCHANGE_AVERAGE_PRICE" if window > 0 else "EXCHANGE_LAST_PRICE", window)
        return ("EXCHANGE_LAST_PRICE", 0)
    except (ValueError, KeyError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class FilterApplicabilityEvidence(EvidenceRecord):
    filter_identity: ContentIdentity
    classifications: tuple[tuple[str, str], ...]
    order_shape: str = "SPOT_BUY_MARKET_UNPRICED_STANDALONE"


def market_filter_applicability(evidence: SymbolFilterEvidence) -> FilterApplicabilityEvidence:
    """Explicit classification for the sole OPS-004 order shape, never arbitrary orders."""
    _, filters = _filters(evidence)
    applicable = {"LOT_SIZE", "MARKET_LOT_SIZE", "MAX_NUM_ORDERS"}
    rows = []
    for kind, item in sorted(filters.items()):
        applies = kind in applicable
        if kind == "NOTIONAL":
            applies = item["applyMinToMarket"] or item["applyMaxToMarket"]
        if kind == "MIN_NOTIONAL":
            applies = "NOTIONAL" not in filters and item["applyToMarket"]
        rows.append((kind, "APPLICABLE" if applies else "NOT_APPLICABLE"))
    return FilterApplicabilityEvidence(evidence.content_identity, tuple(rows))


def market_quantity_rules(evidence: SymbolFilterEvidence) -> dict[str, str]:
    """Resolve disabled MARKET grid using LOT_SIZE; preserve enabled market bounds."""
    _, filters = _filters(evidence)
    lot = filters.get("MARKET_LOT_SIZE", filters.get("LOT_SIZE"))
    if lot is None:
        raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
    result = {k: lot[k] for k in ("minQty", "maxQty", "stepSize")}
    if decimal_field(result["stepSize"]) == 0 and "MARKET_LOT_SIZE" in filters:
        generic = filters.get("LOT_SIZE")
        if generic is None or decimal_field(generic["stepSize"]) <= 0:
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        result["stepSize"] = generic["stepSize"]
        result["minQty"] = str(
            max(decimal_field(result["minQty"]), decimal_field(generic["minQty"]))
        )
        maxima = [
            decimal_field(v) for v in (result["maxQty"], generic["maxQty"]) if decimal_field(v) > 0
        ]
        result["maxQty"] = str(min(maxima)) if maxima else "0"
    return result


MAX_OPEN_ORDERS_EVIDENCE_AGE = timedelta(seconds=10)


@dataclass(frozen=True, slots=True)
class OpenOrdersEvidence(EvidenceRecord):
    symbol: str
    order_ids: tuple[int, ...]
    observed_at: datetime
    source_identity: ContentIdentity
    complete: bool
    environment: str = "TESTNET"


def parse_open_orders(
    payload: object, symbol: str, at: datetime, *, complete: bool
) -> OpenOrdersEvidence:
    """Only a successful complete symbol-scoped read is admissible; no zero fallback."""
    safe_json(payload)
    if (
        complete is not True
        or type(payload) is not list
        or type(at) is not datetime
        or at.tzinfo is not UTC
        or type(symbol) is not str
        or not re.fullmatch(r"[A-Z0-9]{2,30}", symbol)
    ):
        raise EvidenceError("OPEN_ORDERS_EVIDENCE_INVALID")
    ids = []
    for row in payload:
        if (
            type(row) is not dict
            or row.get("symbol") != symbol
            or type(row.get("orderId")) is not int
            or row["orderId"] < 0
            or row.get("status") not in ("NEW", "PARTIALLY_FILLED", "PENDING_CANCEL")
        ):
            raise EvidenceError("OPEN_ORDERS_EVIDENCE_INVALID")
        ids.append(row["orderId"])
    if len(set(ids)) != len(ids):
        raise EvidenceError("OPEN_ORDERS_EVIDENCE_INVALID")
    return OpenOrdersEvidence(
        symbol, tuple(sorted(ids)), at, ContentIdentity.from_canonical(payload), True
    )


def check_order_capacity(filters: object, orders: object, symbol: str, at: object) -> str | None:
    try:
        if not isinstance(filters, SymbolFilterEvidence):
            return "OPEN_ORDERS_EVIDENCE_INVALID"
        data, items = _filters(filters)
        if "MAX_NUM_ORDERS" not in items:
            return None
        if (
            not verify_record(orders, OpenOrdersEvidence)
            or not isinstance(orders, OpenOrdersEvidence)
            or orders.environment != "TESTNET"
            or orders.symbol != symbol
            or data["symbol"] != symbol
            or orders.complete is not True
            or type(at) is not datetime
            or at.tzinfo is not UTC
            or orders.observed_at.tzinfo is not UTC
            or orders.observed_at > at
            or len(set(orders.order_ids)) != len(orders.order_ids)
            or any(i < 0 for i in orders.order_ids)
        ):
            return "OPEN_ORDERS_EVIDENCE_INVALID"
        if at - orders.observed_at > MAX_OPEN_ORDERS_EVIDENCE_AGE:
            return "OPEN_ORDERS_EVIDENCE_STALE"
        if len(orders.order_ids) + 1 > items["MAX_NUM_ORDERS"]["maxNumOrders"]:
            return "ORDER_CAPACITY_EXCEEDED"
        return None
    except (ValueError, TypeError, AttributeError):
        return "OPEN_ORDERS_EVIDENCE_INVALID"
