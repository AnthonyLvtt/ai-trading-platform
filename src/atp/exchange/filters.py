"""CTO MARKET filter policy over explicit offline Exchange evidence. No rounding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
    for item in data["filters"]:
        if type(item) is not dict or type(item.get("filterType")) is not str:
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        kind = item["filterType"]
        if kind not in schemas:
            raise EvidenceError("UNSUPPORTED_SYMBOL_FILTER")
        if kind in result:
            raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
        for key in schemas[kind]:
            decimal_field(item.get(key))
        bounds = schemas[kind]
        if len(bounds) >= 2:
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
    evidence: object, symbol: str, quantity: object, at: object, price: object = None
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
        lot = filters.get("MARKET_LOT_SIZE", filters.get("LOT_SIZE"))
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
            "UNSUPPORTED_SYMBOL_FILTER"
            if isinstance(error, EvidenceError) and error.args == ("UNSUPPORTED_SYMBOL_FILTER",)
            else "SYMBOL_FILTER_INCOMPATIBLE"
        )
