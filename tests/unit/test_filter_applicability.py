"""CTO OPS-004: real metadata, exact grids and fail-closed order capacity."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from atp.exchange.filters import (
    NotionalPriceEvidence,
    check_market_filters,
    check_order_capacity,
    market_filter_applicability,
    market_quantity_rules,
    parse_open_orders,
    parse_symbol_filters,
)
from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.preparation import select_quantity
from atp.shared.identity import ContentIdentity

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def payload():
    return json.loads(Path("tests/fixtures/exchange/btcusdt-exchange-info.json").read_text())[
        "symbols"
    ][0]


def evidence():
    result = parse_symbol_filters(payload(), NOW)
    assert result is not None
    return result


def price(value="50000"):
    return NotionalPriceEvidence(
        "BTCUSDT",
        Decimal(value),
        "EXCHANGE_AVERAGE_PRICE",
        ContentIdentity.from_text("offline price"),
        NOW,
        NOW,
        5,
    )


def orders(rows=None, at=NOW):
    return parse_open_orders([] if rows is None else rows, "BTCUSDT", at, complete=True)


@pytest.mark.parametrize(
    "kind,classification",
    [
        ("PRICE_FILTER", "NOT_APPLICABLE"),
        ("LOT_SIZE", "APPLICABLE"),
        ("MARKET_LOT_SIZE", "APPLICABLE"),
        ("NOTIONAL", "APPLICABLE"),
        ("ICEBERG_PARTS", "NOT_APPLICABLE"),
        ("TRAILING_DELTA", "NOT_APPLICABLE"),
        ("PERCENT_PRICE_BY_SIDE", "NOT_APPLICABLE"),
        ("MAX_NUM_ORDERS", "APPLICABLE"),
        ("MAX_NUM_ORDER_LISTS", "NOT_APPLICABLE"),
        ("MAX_NUM_ALGO_ORDERS", "NOT_APPLICABLE"),
        ("MAX_NUM_ORDER_AMENDS", "NOT_APPLICABLE"),
    ],
)
def test_real_filter_classification(kind, classification):
    assert dict(market_filter_applicability(evidence()).classifications)[kind] == classification


def test_unknown_and_malformed_known_filters_fail_closed():
    data = payload()
    data["filters"].append({"filterType": "FUTURE_FILTER"})
    assert parse_symbol_filters(data, NOW) is None
    for item in payload()["filters"]:
        data = payload()
        broken = deepcopy(item)
        del broken[next(k for k in broken if k != "filterType")]
        data["filters"] = [broken]
        assert parse_symbol_filters(data, NOW) is None


def test_zero_market_step_resolves_generic_grid_and_exact_five():
    rules = market_quantity_rules(evidence())
    assert Decimal(rules["stepSize"]) == Decimal("0.00001")
    assert Decimal(rules["maxQty"]) == Decimal("105.07867116")
    chosen = select_quantity(evidence(), price(), NOW, orders())
    assert chosen.selected_quantity == Decimal("0.0001")
    assert chosen.projected_quote_notional == 5
    assert chosen == select_quantity(evidence(), price(), NOW, orders())
    assert chosen.selected_quantity % chosen.step_size == 0
    assert (chosen.selected_quantity + chosen.step_size) * price().price > 5
    assert check_market_filters(evidence(), "BTCUSDT", Decimal("106"), NOW, price(), orders())
    assert check_market_filters(evidence(), "BTCUSDT", Decimal("0.000101"), NOW, price(), orders())


def test_impossible_exact_five_never_increases_cap():
    with pytest.raises(EvidenceError, match="NO_ADMISSIBLE_QUANTITY"):
        select_quantity(evidence(), price("60000"), NOW, orders())


@pytest.mark.parametrize(
    "age,expected", [(0, None), (10, None), (10.000001, "OPEN_ORDERS_EVIDENCE_STALE")]
)
def test_capacity_freshness(age, expected):
    assert (
        check_order_capacity(evidence(), orders(), "BTCUSDT", NOW + timedelta(seconds=age))
        == expected
    )


def test_capacity_full_missing_future_tampered_and_partial():
    rows = [{"symbol": "BTCUSDT", "orderId": i, "status": "NEW"} for i in range(200)]
    assert (
        check_order_capacity(evidence(), orders(rows), "BTCUSDT", NOW) == "ORDER_CAPACITY_EXCEEDED"
    )
    assert check_order_capacity(evidence(), orders(rows[:-1]), "BTCUSDT", NOW) is None
    assert check_order_capacity(evidence(), None, "BTCUSDT", NOW) == "OPEN_ORDERS_EVIDENCE_INVALID"
    assert check_order_capacity(evidence(), orders(at=NOW + timedelta(seconds=1)), "BTCUSDT", NOW)
    bad = orders()
    object.__setattr__(bad, "symbol", "OTHER")
    assert check_order_capacity(evidence(), bad, "BTCUSDT", NOW)
    with pytest.raises(EvidenceError):
        parse_open_orders([], "BTCUSDT", NOW, complete=False)
    with pytest.raises(EvidenceError):
        orders([{"symbol": "BTCUSDT", "orderId": 1, "status": "UNKNOWN"}])
