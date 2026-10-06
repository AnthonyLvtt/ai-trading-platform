"""Synthetic, offline-only BalanceEx shape checks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from atp.exchange.kraken.extended_balance import (
    ExtendedBalanceShapeError,
    parse_offline_extended_balance,
)
from atp.exchange.kraken.private import KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST


def fixture() -> dict[str, object]:
    return {
        "error": [],
        "result": {
            "XXBT": {
                "balance": "0.2",
                "hold_trade": "0.05",
                "credit": "0",
                "credit_used": "0",
            },
            "ZEUR": {
                "balance": "1000.00",
                "hold_trade": "200.00",
                "credit": "10.00",
                "credit_used": "5.00",
            },
        },
    }


def test_offline_formula_and_route_remains_closed() -> None:
    rows = parse_offline_extended_balance(fixture())
    assert [(row.asset, row.available) for row in rows] == [
        ("BTC", Decimal("0.15")),
        ("EUR", Decimal("805.00")),
    ]
    assert "/0/private/BalanceEx" not in KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda p: p["result"]["ZEUR"].pop("credit"), "BALANCE_EX_FIELDS_INCOMPLETE"),
        (lambda p: p["result"]["ZEUR"].pop("hold_trade"), "BALANCE_EX_FIELDS_INCOMPLETE"),
        (lambda p: p["result"]["ZEUR"].update(credit="NaN"), "BALANCE_EX_AMOUNT_INVALID"),
        (lambda p: p["result"]["ZEUR"].update(balance="-1"), "BALANCE_EX_AMOUNT_INVALID"),
        (lambda p: p["result"]["ZEUR"].update(hold_trade="2000"), "BALANCE_EX_AVAILABLE_NEGATIVE"),
        (lambda p: p["result"].update({"XXBT.F": {}}), "BALANCE_EX_ASSET_UNSUPPORTED"),
    ],
)
def test_missing_or_unsafe_facts_block(change, reason: str) -> None:
    payload = fixture()
    change(payload)
    with pytest.raises(ExtendedBalanceShapeError, match=reason):
        parse_offline_extended_balance(payload)
