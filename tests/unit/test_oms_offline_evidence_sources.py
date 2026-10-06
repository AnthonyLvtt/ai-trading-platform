from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp.exchange.execution import OrderIntent, OrderSide, OrderType
from atp.exchange.kraken.private import KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
from atp.exchange.kraken.trade_volume import (
    TradeVolumeShapeError,
    parse_offline_trade_volume_fee_bound,
)
from atp.oms.evidence_sources import (
    bounded_fee_from_offline_trade_volume,
    spendable_eur_from_offline_balance_ex,
)
from atp.oms.exposure import ExposureError
from atp.shared.identity import ContentIdentity
from atp.strategy.model import SignalKind
from tests.unit.test_kraken_execution_foundation import approved

AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def balance_ex_payload() -> dict[str, object]:
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


def trade_volume_payload() -> dict[str, object]:
    fee_row = {
        "fee": "0.1000",
        "minfee": "0.1000",
        "maxfee": "0.2600",
        "nextfee": None,
        "tiervolume": "10000000.0000",
        "nextvolume": None,
    }
    maker_row = {
        "fee": "0.0000",
        "minfee": "0.0000",
        "maxfee": "0.1600",
        "nextfee": None,
        "tiervolume": "10000000.0000",
        "nextvolume": None,
    }
    return {
        "error": [],
        "result": {
            "currency": "ZEUR",
            "asset_class": "currency",
            "volume": "1000.0000",
            "inputs": {
                "domain_spot_volume_30d": "1000.0000",
                "domain_futures_volume_30d": "0.0000",
                "domain_assets_on_platform": "0.0000",
            },
            "fees": {"XXBTZEUR": fee_row},
            "fees_maker": {"XXBTZEUR": maker_row},
        },
    }


def buy_limit() -> OrderIntent:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    return OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        limit_price=Decimal("95000"),
    )


def test_balance_ex_offline_producer_creates_spendable_eur_without_opening_route() -> None:
    evidence = spendable_eur_from_offline_balance_ex(
        payload=balance_ex_payload(),
        account_identity=ContentIdentity.from_text("account"),
        credential_reference_identity=ContentIdentity.from_text("credential"),
        observed_at=AT,
    )
    assert evidence.amount_eur == Decimal("805.00")
    assert evidence.side_effect_performed is False
    assert "/0/private/BalanceEx" not in KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST


def test_trade_volume_uses_maximum_taker_fee_for_limit_candidate() -> None:
    bound = parse_offline_trade_volume_fee_bound(trade_volume_payload())
    assert bound.native_pair == "XXBTZEUR"
    assert bound.taker_max_fee_percent == Decimal("0.2600")

    evidence = bounded_fee_from_offline_trade_volume(
        payload=trade_volume_payload(),
        account_identity=ContentIdentity.from_text("account"),
        intent=buy_limit(),
        observed_at=AT,
    )
    assert evidence.maximum_fee_eur == Decimal("0.247000")
    assert "/0/private/TradeVolume" not in KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST


def test_fee_parser_rejects_pair_or_fee_shapes_that_cannot_bound_btc_eur() -> None:
    payload = trade_volume_payload()
    result = payload["result"]
    assert isinstance(result, dict)
    fees = result["fees"]
    assert isinstance(fees, dict)
    fees["XXBTZUSD"] = fees.pop("XXBTZEUR")
    with pytest.raises(TradeVolumeShapeError, match="TRADE_VOLUME_PAIR_INVALID"):
        parse_offline_trade_volume_fee_bound(payload)

    payload = trade_volume_payload()
    result = payload["result"]
    assert isinstance(result, dict)
    maker = result["fees_maker"]
    assert isinstance(maker, dict)
    row = maker["XXBTZEUR"]
    assert isinstance(row, dict)
    row["maxfee"] = "0.3000"
    with pytest.raises(TradeVolumeShapeError, match="TRADE_VOLUME_FEE_ORDER_INVALID"):
        parse_offline_trade_volume_fee_bound(payload)


def test_fee_producer_rejects_non_buy_limit_candidate() -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    market = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )
    with pytest.raises(ExposureError, match="BOUNDED_FEE_SOURCE_CANDIDATE_INVALID"):
        bounded_fee_from_offline_trade_volume(
            payload=trade_volume_payload(),
            account_identity=ContentIdentity.from_text("account"),
            intent=market,
            observed_at=AT,
        )
