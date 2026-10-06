"""Offline-only interpretation of Kraken Spot TradeVolume fee data.

This module has no signer, credential, transport, or route authority. It only
parses synthetic/documented response shapes so OMS can reason about a
conservative fee bound before any runtime source is qualified.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class TradeVolumeShapeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OfflineTradeFeeBound:
    native_pair: str
    taker_max_fee_percent: Decimal

    def __post_init__(self) -> None:
        if (
            self.native_pair != "XXBTZEUR"
            or type(self.taker_max_fee_percent) is not Decimal
            or not self.taker_max_fee_percent.is_finite()
            or not Decimal("0") <= self.taker_max_fee_percent <= Decimal("100")
        ):
            raise TradeVolumeShapeError("TRADE_VOLUME_FEE_BOUND_INVALID")


def _percent(value: object) -> Decimal:
    if type(value) is not str:
        raise TradeVolumeShapeError("TRADE_VOLUME_PERCENT_INVALID")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise TradeVolumeShapeError("TRADE_VOLUME_PERCENT_INVALID") from None
    if not result.is_finite() or not Decimal("0") <= result <= Decimal("100"):
        raise TradeVolumeShapeError("TRADE_VOLUME_PERCENT_INVALID")
    return result


def parse_offline_trade_volume_fee_bound(payload: object) -> OfflineTradeFeeBound:
    """Extract the documented maximum taker fee for BTC/EUR.

    Kraken documents maker/taker schedules with taker data under the fees field
    and exposes maxfee on that schedule entry. ATP deliberately uses the taker
    maximum because a LIMIT order may execute immediately as taker.
    """
    if (
        type(payload) is not dict
        or set(payload) != {"error", "result"}
        or type(payload["error"]) is not list
        or payload["error"]
        or type(payload["result"]) is not dict
    ):
        raise TradeVolumeShapeError("TRADE_VOLUME_ENVELOPE_INVALID")
    result = payload["result"]
    required = {"currency", "asset_class", "volume", "inputs", "fees", "fees_maker"}
    if not required.issubset(result):
        raise TradeVolumeShapeError("TRADE_VOLUME_FIELDS_INCOMPLETE")
    fees = result["fees"]
    maker = result["fees_maker"]
    if type(fees) is not dict or type(maker) is not dict:
        raise TradeVolumeShapeError("TRADE_VOLUME_FEES_INVALID")
    if set(fees) != {"XXBTZEUR"} or set(maker) != {"XXBTZEUR"}:
        raise TradeVolumeShapeError("TRADE_VOLUME_PAIR_INVALID")
    taker_row = fees["XXBTZEUR"]
    maker_row = maker["XXBTZEUR"]
    required_fee_fields = {
        "fee",
        "minfee",
        "maxfee",
        "nextfee",
        "tiervolume",
        "nextvolume",
    }
    if (
        type(taker_row) is not dict
        or type(maker_row) is not dict
        or set(taker_row) != required_fee_fields
        or set(maker_row) != required_fee_fields
    ):
        raise TradeVolumeShapeError("TRADE_VOLUME_FEE_FIELDS_INVALID")
    taker_max = _percent(taker_row["maxfee"])
    maker_max = _percent(maker_row["maxfee"])
    if maker_max > taker_max:
        raise TradeVolumeShapeError("TRADE_VOLUME_FEE_ORDER_INVALID")
    return OfflineTradeFeeBound("XXBTZEUR", taker_max)
