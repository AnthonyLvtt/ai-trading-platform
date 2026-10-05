from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from atp.exchange.contracts import BTC_EUR, PublicObservation
from atp.exchange.execution import ExecutionError, OrderIntent, OrderSide, OrderType
from atp.exchange.kraken.preflight import KrakenOrderPreflight
from atp.exchange.kraken.public import parse_asset_pairs, parse_ticker_price
from atp.strategy.model import SignalKind
from tests.unit.test_kraken_execution_foundation import approved

FIXTURES = Path("tests/fixtures/kraken")
AT = datetime(2026, 9, 24, 12, 0, 1, tzinfo=UTC)


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def evidence():
    mapping, metadata = parse_asset_pairs(fixture("asset-pairs.json"), BTC_EUR, AT)
    observation = PublicObservation(AT - timedelta(seconds=1), AT)
    price = parse_ticker_price(
        fixture("ticker.json"),
        BTC_EUR,
        mapping,
        AT,
        observation,
    )
    return mapping, metadata, price


def intent(
    *,
    signal: SignalKind = SignalKind.LONG_ENTRY,
    order_type: OrderType = OrderType.LIMIT,
    quantity: Decimal = Decimal("0.001"),
    price: Decimal | None = Decimal("95000.0"),
) -> OrderIntent:
    evaluation, decision = approved(signal)
    return OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY if signal is SignalKind.LONG_ENTRY else OrderSide.SELL,
        order_type=order_type,
        quantity=quantity,
        limit_price=price if order_type is OrderType.LIMIT else None,
    )


def test_limit_preflight_is_deterministic_and_uses_qualified_native_mapping() -> None:
    mapping, metadata, _ = evidence()
    order = intent()
    first = KrakenOrderPreflight.build(
        intent=order,
        mapping=mapping,
        metadata=metadata,
    )
    second = KrakenOrderPreflight.build(
        intent=order,
        mapping=mapping,
        metadata=metadata,
    )
    payload = dict(first.payload)
    assert first.content_identity == second.content_identity
    assert first.client_order_id == second.client_order_id
    assert payload == {
        "cl_ord_id": first.client_order_id,
        "ordertype": "limit",
        "pair": "XXBTZEUR",
        "price": "95000",
        "type": "buy",
        "volume": "0.001",
    }


def test_market_preflight_requires_fresh_bound_price_for_notional_check() -> None:
    mapping, metadata, price = evidence()
    order = intent(order_type=OrderType.MARKET, price=None)
    result = KrakenOrderPreflight.build(
        intent=order,
        mapping=mapping,
        metadata=metadata,
        market_price=price,
    )
    assert dict(result.payload)["ordertype"] == "market"
    assert "price" not in dict(result.payload)

    with pytest.raises(ExecutionError, match="KRAKEN_MARKET_PRICE_REQUIRED"):
        KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)

    stale = replace(price, freshness="UNKNOWN", observation=None)
    with pytest.raises(ExecutionError, match="KRAKEN_PRICE_EVIDENCE_INVALID"):
        KrakenOrderPreflight.build(
            intent=order,
            mapping=mapping,
            metadata=metadata,
            market_price=stale,
        )


def test_preflight_rejects_foreign_or_stale_mapping_metadata_binding() -> None:
    mapping, metadata, _ = evidence()
    order = intent()
    offline = replace(mapping, instrument_status="cancel_only")
    with pytest.raises(ExecutionError, match="KRAKEN_MAPPING_NOT_AUTHORIZED"):
        KrakenOrderPreflight.build(intent=order, mapping=offline, metadata=metadata)

    detached = replace(metadata, mapping_identity=metadata.source_identity)
    with pytest.raises(ExecutionError, match="KRAKEN_METADATA_NOT_AUTHORIZED"):
        KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=detached)


@pytest.mark.parametrize(
    ("quantity", "price", "reason"),
    [
        (Decimal("0.00001"), Decimal("95000.0"), "KRAKEN_QUANTITY_BELOW_MINIMUM"),
        (Decimal("0.001000001"), Decimal("95000.0"), "KRAKEN_QUANTITY_INCREMENT_INVALID"),
        (Decimal("0.001"), Decimal("95000.05"), "KRAKEN_PRICE_INCREMENT_INVALID"),
    ],
)
def test_preflight_enforces_public_metadata_constraints(
    quantity: Decimal,
    price: Decimal,
    reason: str,
) -> None:
    mapping, metadata, _ = evidence()
    with pytest.raises(ExecutionError, match=reason):
        KrakenOrderPreflight.build(
            intent=intent(quantity=quantity, price=price),
            mapping=mapping,
            metadata=metadata,
        )


def test_minimum_notional_is_fail_closed() -> None:
    mapping, metadata, _ = evidence()
    strict = replace(metadata, minimum_notional=Decimal("1000"))
    with pytest.raises(ExecutionError, match="KRAKEN_NOTIONAL_BELOW_MINIMUM"):
        KrakenOrderPreflight.build(
            intent=intent(quantity=Decimal("0.001"), price=Decimal("95000.0")),
            mapping=mapping,
            metadata=strict,
        )


def test_tampered_preflight_fails_validation() -> None:
    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(
        intent=intent(),
        mapping=mapping,
        metadata=metadata,
    )
    object.__setattr__(result, "client_order_id", "tampered")
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()
