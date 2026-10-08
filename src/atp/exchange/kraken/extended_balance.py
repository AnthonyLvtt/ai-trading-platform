"""Offline-only interpretation of a Kraken Spot BalanceEx fixture.

This module has no signer, route, credential, transport, or trading port. Its
output is unqualified data and cannot authorize an order or a real private read.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, InvalidOperation, localcontext


class ExtendedBalanceShapeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OfflineExtendedBalance:
    asset: str
    balance: Decimal
    hold_trade: Decimal
    credit: Decimal
    credit_used: Decimal
    available: Decimal

    def __post_init__(self) -> None:
        parts = (self.balance, self.hold_trade, self.credit, self.credit_used)
        if (
            self.asset not in {"BTC", "EUR"}
            or any(type(part) is not Decimal or not part.is_finite() or part < 0 for part in parts)
            or type(self.available) is not Decimal
            or not self.available.is_finite()
            or self.available < 0
            or self.available
            != _exact_available(self.balance, self.credit, self.credit_used, self.hold_trade)
        ):
            raise ExtendedBalanceShapeError("BALANCE_EX_ROW_INVALID")


def _exact_available(
    balance: Decimal, credit: Decimal, credit_used: Decimal, hold_trade: Decimal
) -> Decimal:
    try:
        with localcontext() as context:
            context.prec = 100
            context.traps[Inexact] = True
            return balance + credit - credit_used - hold_trade
    except DecimalException:
        raise ExtendedBalanceShapeError("BALANCE_EX_ARITHMETIC_UNSAFE") from None


def _amount(value: object) -> Decimal:
    if not isinstance(value, str | int | Decimal) or type(value) is bool:
        raise ExtendedBalanceShapeError("BALANCE_EX_AMOUNT_INVALID")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ExtendedBalanceShapeError("BALANCE_EX_AMOUNT_INVALID") from None
    if not amount.is_finite() or amount < 0:
        raise ExtendedBalanceShapeError("BALANCE_EX_AMOUNT_INVALID")
    return amount


def parse_offline_extended_balance(payload: object) -> tuple[OfflineExtendedBalance, ...]:
    """Parse observed BTC/EUR rows without synthesizing absent assets.

    Kraken's public example omits credit fields. We do not infer zero from an
    omission, so such a response cannot establish spendable funds here. An
    absent EUR row remains absent; callers classify it separately.
    """
    if (
        type(payload) is not dict
        or set(payload) != {"error", "result"}
        or type(payload["error"]) is not list
        or payload["error"]
        or type(payload["result"]) is not dict
    ):
        raise ExtendedBalanceShapeError("BALANCE_EX_ENVELOPE_INVALID")
    result = payload["result"]
    if not set(result) <= {"XXBT", "ZEUR"}:
        raise ExtendedBalanceShapeError("BALANCE_EX_ASSET_UNSUPPORTED")
    rows = []
    for native_asset in sorted(result):
        raw = result[native_asset]
        if type(raw) is not dict or set(raw) != {"balance", "hold_trade", "credit", "credit_used"}:
            raise ExtendedBalanceShapeError("BALANCE_EX_FIELDS_INCOMPLETE")
        balance = _amount(raw["balance"])
        hold_trade = _amount(raw["hold_trade"])
        credit = _amount(raw["credit"])
        credit_used = _amount(raw["credit_used"])
        available = _exact_available(balance, credit, credit_used, hold_trade)
        if available < 0:
            raise ExtendedBalanceShapeError("BALANCE_EX_AVAILABLE_NEGATIVE")
        rows.append(
            OfflineExtendedBalance(
                asset={"XXBT": "BTC", "ZEUR": "EUR"}[native_asset],
                balance=balance,
                hold_trade=hold_trade,
                credit=credit,
                credit_used=credit_used,
                available=available,
            )
        )
    return tuple(rows)
