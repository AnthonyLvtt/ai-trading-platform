"""Pure read-only preparation projections for the CTO BTCUSDT 20/50/5m procedure."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction

from atp.exchange.filters import (
    NotionalPriceEvidence,
    SymbolFilterEvidence,
    check_market_filters,
    market_notional_price_contract,
)
from atp.exchange.read_only import (
    EvidenceError,
    EvidenceRecord,
    decimal_field,
    safe_json,
    verify_record,
)
from atp.risk.identity import PositionId
from atp.risk.model import OpenPosition, PortfolioKnowledgeStatus, PortfolioState, PositionSide
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class QuantitySelectionEvidence(EvidenceRecord):
    price: Decimal
    price_evidence_identity: ContentIdentity
    quantity_filter_identity: ContentIdentity
    step_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    selected_quantity: Decimal
    projected_quote_notional: Decimal
    filter_evidence_identity: ContentIdentity
    symbol: str = "BTCUSDT"
    quote_cap: Decimal = Decimal("5")
    selection_policy: str = "MAX_ADMISSIBLE_UNDER_QUOTE_CAP"


def select_quantity(filters: object, price: object, at: datetime) -> QuantitySelectionEvidence:
    """Select an integer grid index exactly; never round an existing order quantity."""
    if not verify_record(filters, SymbolFilterEvidence) or not verify_record(
        price, NotionalPriceEvidence
    ):
        raise EvidenceError("NO_ADMISSIBLE_QUANTITY")
    assert isinstance(filters, SymbolFilterEvidence) and isinstance(price, NotionalPriceEvidence)
    if (
        price.price <= 0
        or price.symbol != "BTCUSDT"
        or market_notional_price_contract(filters) != (price.source_type, price.avg_price_minutes)
    ):
        raise EvidenceError("NO_ADMISSIBLE_QUANTITY")
    raw = json.loads(filters.payload)
    items = {item["filterType"]: item for item in raw["filters"]}
    lot = items.get("MARKET_LOT_SIZE", items.get("LOT_SIZE"))
    if lot is None:
        raise EvidenceError("NO_ADMISSIBLE_QUANTITY")
    low, high, step = (decimal_field(lot[k]) for k in ("minQty", "maxQty", "stepSize"))
    if step <= 0:
        # No finite grid is established; do not invent a precision or fallback step.
        raise EvidenceError("NO_ADMISSIBLE_QUANTITY")
    ceiling = Fraction(5) / Fraction(price.price)
    if high > 0:
        ceiling = min(ceiling, Fraction(high))
    notional = items.get("NOTIONAL")
    if notional is not None and notional["applyMaxToMarket"]:
        ceiling = min(
            ceiling, Fraction(decimal_field(notional["maxNotional"])) / Fraction(price.price)
        )
    index = ceiling // Fraction(step)
    with localcontext() as context:
        context.prec = (
            len(str(index)) + len(step.as_tuple().digits) + len(price.price.as_tuple().digits) + 10
        )
        quantity = Decimal(index) * step
        projected = quantity * price.price
    if (
        quantity <= 0
        or projected > 5
        or check_market_filters(filters, "BTCUSDT", quantity, at, price) is not None
    ):
        raise EvidenceError("NO_ADMISSIBLE_QUANTITY")
    return QuantitySelectionEvidence(
        price.price,
        price.content_identity,
        ContentIdentity.from_canonical(lot),
        step,
        low,
        high,
        quantity,
        projected,
        filters.content_identity,
    )


@dataclass(frozen=True, slots=True)
class TestnetPortfolioEvidence(EvidenceRecord):
    __test__ = False
    btc_free: Decimal
    btc_locked: Decimal
    usdt_free: Decimal
    usdt_locked: Decimal
    open_order_count: int
    account_identity: ContentIdentity
    orders_identity: ContentIdentity
    observed_at: datetime
    state: str
    environment: str = "TESTNET"


def portfolio_evidence(account: object, orders: object, at: datetime) -> TestnetPortfolioEvidence:
    """Current positive balances/open orders are exposure, never an assumed empty account."""
    safe_json(account)
    safe_json(orders)
    if (
        type(account) is not dict
        or type(orders) is not list
        or type(account.get("balances")) is not list
    ):
        raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
    balances: dict[str, tuple[Decimal, Decimal]] = {}
    for row in account["balances"]:
        if type(row) is not dict or type(row.get("asset")) is not str or row["asset"] in balances:
            raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
        balances[row["asset"]] = (decimal_field(row.get("free")), decimal_field(row.get("locked")))
    if "BTC" not in balances or "USDT" not in balances:
        raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
    ids = set()
    for order in orders:
        if (
            type(order) is not dict
            or order.get("symbol") != "BTCUSDT"
            or type(order.get("orderId")) is not int
            or order["orderId"] in ids
        ):
            raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
        ids.add(order["orderId"])
    btc, usdt = balances["BTC"], balances["USDT"]
    return TestnetPortfolioEvidence(
        *btc,
        *usdt,
        len(orders),
        ContentIdentity.from_canonical(account),
        ContentIdentity.from_canonical(orders),
        at,
        "OPEN_LONG" if any(btc) or orders else "EMPTY",
    )


def portfolio_for_risk(evidence: TestnetPortfolioEvidence) -> PortfolioState:
    if not verify_record(evidence, TestnetPortfolioEvidence) or evidence.environment != "TESTNET":
        return PortfolioState.create(PortfolioKnowledgeStatus.UNKNOWN)
    exposure = bool(evidence.btc_free or evidence.btc_locked or evidence.open_order_count)
    if evidence.state != ("OPEN_LONG" if exposure else "EMPTY"):
        return PortfolioState.create(PortfolioKnowledgeStatus.UNKNOWN)
    return PortfolioState.create(
        PortfolioKnowledgeStatus.KNOWN_OPEN if exposure else PortfolioKnowledgeStatus.KNOWN_EMPTY,
        (OpenPosition(PositionId(str(evidence.content_identity)), "BTCUSDT", PositionSide.LONG),)
        if exposure
        else (),
        source_evidence_identity=evidence.content_identity,
    )
