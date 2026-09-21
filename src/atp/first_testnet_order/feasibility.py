"""ENG-TO-OPS-006 pre-watch quantity feasibility. Read-only and never an authorization.

Answers one question before a watcher session consumes an ActivationGrant window: does at
least one exact quantity exist, right now, for the CTO BTCUSDT BUY MARKET procedure?
No Strategy, portfolio, grant, pin, credential or economic path is involved.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from math import ceil, floor

from atp.exchange.filters import (
    NotionalPriceEvidence,
    SymbolFilterEvidence,
    check_market_quantity,
    market_notional_bounds,
    market_notional_price_contract,
    market_quantity_rules,
    parse_symbol_filters,
)
from atp.exchange.read_only import (
    EvidenceError,
    EvidenceRecord,
    decimal_field,
    encoded,
    timestamp,
    verify_record,
)
from atp.first_testnet_order.preparation_runtime import ReadOnlyClock, ReadOnlySource
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity

SYMBOL = "BTCUSDT"
# The CTO first-order projection cap. Never derived, widened or tolerance-adjusted.
FIRST_ORDER_QUOTE_CAP = Decimal("5")
FILTER_EVIDENCE_MAX_AGE = timedelta(minutes=15)
PRICE_EVIDENCE_MAX_AGE = timedelta(seconds=10)
NO_ADMISSIBLE_QUANTITY = "NO_ADMISSIBLE_QUANTITY"
# Closed reason vocabulary: nothing else crossing a network boundary reaches a report.
SAFE_BLOCK_REASONS = frozenset(
    {
        "FIRST_ORDER_NOT_READY",
        "INVALID_FILTER_EVIDENCE",
        "SYMBOL_FILTER_EVIDENCE_STALE",
        "SYMBOL_FILTER_INCOMPATIBLE",
        "UNSUPPORTED_SYMBOL_FILTER",
        "PRICE_EVIDENCE_STALE",
        "TIME_EVIDENCE_INVALID",
        "READ_ONLY_SOURCE_UNAVAILABLE",
    }
)


class FeasibilityStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    NOT_FEASIBLE = "NOT_FEASIBLE"


@dataclass(frozen=True, slots=True)
class QuantityFeasibilityEvidence(EvidenceRecord):
    """A point-in-time existence proof. FEASIBLE is not an admissible future order."""

    status: FeasibilityStatus
    reason_code: str | None
    avg_price: Decimal
    projected_price: Decimal
    avg_price_evidence_identity: ContentIdentity
    projected_price_evidence_identity: ContentIdentity
    filter_evidence_identity: ContentIdentity
    grid_step: Decimal
    grid_index_min: int
    grid_index_max: int
    witness_quantity: Decimal | None
    evaluated_at: datetime
    symbol: str = SYMBOL
    quote_cap: Decimal = FIRST_ORDER_QUOTE_CAP
    submission_authorized: bool = False
    side_effect_performed: bool = False


def _check_price(price: object, at: datetime) -> NotionalPriceEvidence:
    if not verify_record(price, NotionalPriceEvidence):
        raise EvidenceError("FIRST_ORDER_NOT_READY")
    assert isinstance(price, NotionalPriceEvidence)
    if not price.effective_at <= price.observed_at <= at:
        raise EvidenceError("TIME_EVIDENCE_INVALID")
    if at - price.effective_at > PRICE_EVIDENCE_MAX_AGE:
        raise EvidenceError("PRICE_EVIDENCE_STALE")
    if price.symbol != SYMBOL or price.price <= 0:
        raise EvidenceError("FIRST_ORDER_NOT_READY")
    return price


def assess_quantity_feasibility(
    filters: object, avg_price: object, projected_price: object, at: datetime
) -> QuantityFeasibilityEvidence:
    """Exact existence test with Fractions; invalid or stale evidence raises (BLOCKED).

    A quantity q is admissible when q > 0, q lies on the applicable grid and bounds,
    q * avg_price meets the applicable NOTIONAL bounds, and q * projected_price <= cap.
    """
    if type(at) is not datetime or at.tzinfo is not UTC:
        raise EvidenceError("TIME_EVIDENCE_INVALID")
    if not verify_record(filters, SymbolFilterEvidence):
        raise EvidenceError("INVALID_FILTER_EVIDENCE")
    assert isinstance(filters, SymbolFilterEvidence)
    if filters.observed_at > at:
        raise EvidenceError("INVALID_FILTER_EVIDENCE")
    if at - filters.observed_at > FILTER_EVIDENCE_MAX_AGE:
        raise EvidenceError("SYMBOL_FILTER_EVIDENCE_STALE")
    average = _check_price(avg_price, at)
    projected = _check_price(projected_price, at)
    # Both economic calculations (NOTIONAL bounds and cap projection) must rest on Exchange
    # price evidence compatible with the applicable MARKET contract; a projection price from
    # any other source or window would open a wider capacity than the one authorized.
    contract = market_notional_price_contract(filters)
    for evidence in (average, projected):
        if contract != (evidence.source_type, evidence.avg_price_minutes):
            raise EvidenceError("FIRST_ORDER_NOT_READY")
    # Unknown or malformed filters raise from the shared parser: BLOCKED, never ignored.
    lot = market_quantity_rules(filters)
    minimum_notional, maximum_notional = market_notional_bounds(filters)
    data = json.loads(filters.payload)
    if (
        data["symbol"] != SYMBOL
        or data["status"] != "TRADING"
        or "MARKET" not in data["orderTypes"]
    ):
        raise EvidenceError("INVALID_FILTER_EVIDENCE")
    step = decimal_field(lot["stepSize"])
    low, high = Fraction(decimal_field(lot["minQty"])), Fraction(decimal_field(lot["maxQty"]))
    average_price, projected_amount = Fraction(average.price), Fraction(projected.price)

    def verdict(
        index_min: int, index_max: int, witness: Decimal | None
    ) -> QuantityFeasibilityEvidence:
        feasible = witness is not None
        return QuantityFeasibilityEvidence(
            FeasibilityStatus.FEASIBLE if feasible else FeasibilityStatus.NOT_FEASIBLE,
            None if feasible else NO_ADMISSIBLE_QUANTITY,
            average.price,
            projected.price,
            average.content_identity,
            projected.content_identity,
            filters.content_identity,
            step,
            index_min,
            index_max,
            witness,
            at,
        )

    if step <= 0:
        # No finite grid is established; a precision or step is never invented.
        return verdict(1, 0, None)
    lowers = [Fraction(0)]
    uppers = [Fraction(FIRST_ORDER_QUOTE_CAP) / projected_amount]
    if low > 0:
        lowers.append(low)
    if high > 0:
        uppers.append(high)
    if minimum_notional is not None:
        lowers.append(minimum_notional / average_price)
    if maximum_notional is not None:
        uppers.append(maximum_notional / average_price)
    grid = Fraction(step)
    index_min = max(1, ceil(max(lowers) / grid))
    index_max = floor(min(uppers) / grid)
    if index_min > index_max:
        return verdict(index_min, index_max, None)
    with localcontext() as context:
        context.prec = len(str(index_max)) + len(step.as_tuple().digits) + 10
        witness = Decimal(index_max) * step
    # Defence in depth: the shared MARKET filter logic must agree with the closed form.
    if (
        Fraction(witness) != index_max * grid
        or Fraction(witness) * projected_amount > FIRST_ORDER_QUOTE_CAP
        or check_market_quantity(filters, SYMBOL, witness, at, average) is not None
    ):
        raise EvidenceError("SYMBOL_FILTER_INCOMPATIBLE")
    return verdict(index_min, index_max, witness)


def check_pre_watch_feasibility(
    *, source: ReadOnlySource, now: Callable[[], datetime]
) -> dict[str, object]:
    """Public read-only GETs only: server time, exchangeInfo and avgPrice.

    No credential, Strategy evaluation, portfolio assumption, ActivationGrant, trust pin
    or economic call. One current average-price observation is both the compatible NOTIONAL
    price and the cap projection price, exactly as the final gate uses it today.
    """
    report: dict[str, object] = {
        "check": "PRE_WATCH_QUANTITY_FEASIBILITY",
        "status": "BLOCKED",
        "reason_code": "FIRST_ORDER_NOT_READY",
        "quote_cap": str(FIRST_ORDER_QUOTE_CAP),
        "strategy_evaluated": False,
        "grant_created": False,
        "trust_pin_requested": False,
        "submission_authorized": False,
        "real_economic_calls": 0,
        "LIVE": "LIVE_FORBIDDEN",
    }
    try:
        clock = ReadOnlyClock(source, now)
        clock.read()
        metadata = source.read("exchangeInfo", (("symbol", SYMBOL),))
        if (
            type(metadata) is not dict
            or type(metadata.get("symbols")) is not list
            or len(metadata["symbols"]) != 1
        ):
            raise EvidenceError("INVALID_FILTER_EVIDENCE")
        symbol = metadata["symbols"][0]
        if (
            type(symbol) is not dict
            or symbol.get("baseAsset") != "BTC"
            or symbol.get("quoteAsset") != "USDT"
            or symbol.get("isSpotTradingAllowed") is not True
        ):
            raise EvidenceError("INVALID_FILTER_EVIDENCE")
        filters = parse_symbol_filters(symbol, now())
        contract = None if filters is None else market_notional_price_contract(filters)
        if filters is None or contract is None:
            raise EvidenceError("INVALID_FILTER_EVIDENCE")
        kind, window = contract
        if kind != "EXCHANGE_AVERAGE_PRICE":
            # Only the exchange average price is a compatible, read-only NOTIONAL proof here.
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        value = source.read("avgPrice", (("symbol", SYMBOL),))
        if type(value) is not dict or type(value.get("mins")) is not int or value["mins"] != window:
            raise EvidenceError("PRICE_EVIDENCE_STALE")
        price = NotionalPriceEvidence(
            SYMBOL,
            decimal_field(value.get("price")),
            kind,
            ContentIdentity.from_canonical(value),
            now(),
            timestamp(value.get("closeTime")),
            window,
        )
        at = clock.read().gate_evaluation_time
        evidence = assess_quantity_feasibility(filters, price, price, at)
    except (EvidenceError, ValidationError) as error:
        if (
            len(error.args) == 1
            and type(error.args[0]) is str
            and error.args[0] in SAFE_BLOCK_REASONS
        ):
            report["reason_code"] = error.args[0]
        return report
    report.update(
        status=evidence.status.value,
        reason_code=evidence.reason_code,
        feasibility=encoded(evidence),
        feasibility_identity=str(evidence.content_identity),
    )
    return report
