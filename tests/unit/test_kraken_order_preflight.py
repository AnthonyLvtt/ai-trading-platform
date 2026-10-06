from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
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
        evaluated_at=AT,
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
            evaluated_at=AT,
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


def rehash(result: KrakenOrderPreflight) -> None:
    """Model a caller that can replace both content and its untrusted digest."""
    from atp.shared.identity import ContentIdentity

    value = {
        "client_order_id": result.client_order_id,
        "intent_identity": str(result.intent_identity),
        "mapping_identity": str(result.mapping_identity),
        "metadata_identity": str(result.metadata_identity),
        "payload": result.payload,
        "price_evidence_identity": (
            None if result.price_evidence_identity is None else str(result.price_evidence_identity)
        ),
    }
    object.__setattr__(result, "content_identity", ContentIdentity.from_canonical(value))


@pytest.mark.parametrize(
    "field",
    [
        "intent_identity",
        "mapping_identity",
        "metadata_identity",
        "price_evidence_identity",
        "content_identity",
    ],
)
def test_every_identity_is_integrity_checked(field: str) -> None:
    from atp.shared.identity import ContentIdentity

    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(intent=intent(), mapping=mapping, metadata=metadata)
    object.__setattr__(result, field, ContentIdentity.from_text("altered"))
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("volume", "0.002"),
        ("price", "96000"),
        ("pair", "XETHZEUR"),
        ("type", "sell"),
    ],
)
def test_valid_looking_payload_tampering_is_detected(key: str, value: str) -> None:
    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(intent=intent(), mapping=mapping, metadata=metadata)
    payload = dict(result.payload)
    payload[key] = value
    object.__setattr__(result, "payload", tuple(sorted(payload.items())))
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1e-3", "0.0010", "abc"])
@pytest.mark.parametrize("key", ["volume", "price"])
def test_rehashed_noncanonical_numbers_are_rejected(key: str, value: str) -> None:
    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(intent=intent(), mapping=mapping, metadata=metadata)
    payload = dict(result.payload)
    payload[key] = value
    object.__setattr__(result, "payload", tuple(sorted(payload.items())))
    rehash(result)
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "reorder", "list", "malformed"])
def test_payload_shape_is_strict(mutation: str) -> None:
    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(intent=intent(), mapping=mapping, metadata=metadata)
    payloads = {
        "extra": tuple(sorted(result.payload + (("leverage", "2"),))),
        "duplicate": result.payload + (result.payload[0],),
        "reorder": tuple(reversed(result.payload)),
        "list": list(result.payload),
        "malformed": (("volume",),),
    }
    object.__setattr__(result, "payload", payloads[mutation])
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


@pytest.mark.parametrize("order_type", list(OrderType))
@pytest.mark.parametrize("signal", [SignalKind.LONG_ENTRY, SignalKind.EXIT])
def test_revalidation_preserves_valid_orders(order_type: OrderType, signal: SignalKind) -> None:
    mapping, metadata, price = evidence()
    order = intent(order_type=order_type, signal=signal)
    result = KrakenOrderPreflight.build(
        intent=order,
        mapping=mapping,
        metadata=metadata,
        market_price=price,
        evaluated_at=AT,
    )
    result.validate_against(
        intent=order, mapping=mapping, metadata=metadata, market_price=price, evaluated_at=AT
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("volume", "0.002"),
        ("price", "96000"),
        ("pair", "XETHZEUR"),
        ("type", "sell"),
    ],
)
def test_rehash_cannot_bypass_source_binding(key: str, value: str) -> None:
    mapping, metadata, _ = evidence()
    order = intent()
    result = KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)
    payload = dict(result.payload)
    payload[key] = value
    object.__setattr__(result, "payload", tuple(sorted(payload.items())))
    rehash(result)
    result.validate()  # Internal consistency alone does not establish source binding.
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_BINDING_INVALID"):
        result.validate_against(intent=order, mapping=mapping, metadata=metadata)


def test_revalidation_reruns_constraints_and_rejects_other_evidence() -> None:
    mapping, metadata, _ = evidence()
    order = intent()
    result = KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)
    with pytest.raises(ExecutionError, match="KRAKEN_NOTIONAL_BELOW_MINIMUM"):
        result.validate_against(
            intent=order,
            mapping=mapping,
            metadata=replace(metadata, minimum_notional=Decimal("1000")),
        )
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_BINDING_INVALID"):
        result.validate_against(
            intent=order,
            mapping=mapping,
            metadata=replace(metadata, minimum_notional=Decimal("1")),
        )


def test_validated_preflight_does_not_enable_submission() -> None:
    from atp.exchange.kraken.execution import DisabledKrakenEconomicTransport

    mapping, metadata, _ = evidence()
    order = intent()
    result = KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)
    result.validate_against(intent=order, mapping=mapping, metadata=metadata)
    with pytest.raises(ExecutionError, match="KRAKEN_ECONOMIC_EXECUTION_NOT_QUALIFIED"):
        DisabledKrakenEconomicTransport().submit(order)


@pytest.mark.parametrize(
    "field",
    [
        "intent_identity",
        "mapping_identity",
        "metadata_identity",
        "price_evidence_identity",
        "content_identity",
    ],
)
def test_wrong_identity_types_fail_closed(field: str) -> None:
    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(intent=intent(), mapping=mapping, metadata=metadata)
    object.__setattr__(result, field, "not-an-identity")
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


def test_rehashed_detached_client_id_fails_closed() -> None:
    mapping, metadata, _ = evidence()
    result = KrakenOrderPreflight.build(intent=intent(), mapping=mapping, metadata=metadata)
    client_id = "atp-" + "0" * 14
    assert client_id != result.client_order_id
    object.__setattr__(result, "client_order_id", client_id)
    payload = dict(result.payload)
    payload["cl_ord_id"] = client_id
    object.__setattr__(result, "payload", tuple(sorted(payload.items())))
    rehash(result)
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


def test_rehashed_market_without_price_evidence_fails_closed() -> None:
    mapping, metadata, price = evidence()
    result = KrakenOrderPreflight.build(
        intent=intent(order_type=OrderType.MARKET),
        mapping=mapping,
        metadata=metadata,
        market_price=price,
        evaluated_at=AT,
    )
    object.__setattr__(result, "price_evidence_identity", None)
    rehash(result)
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_INVALID"):
        result.validate()


def test_revalidation_rejects_different_intent_and_price_evidence() -> None:
    mapping, metadata, price = evidence()
    order = intent(order_type=OrderType.MARKET)
    result = KrakenOrderPreflight.build(
        intent=order,
        mapping=mapping,
        metadata=metadata,
        market_price=price,
        evaluated_at=AT,
    )
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_BINDING_INVALID"):
        result.validate_against(
            intent=intent(order_type=OrderType.MARKET, quantity=Decimal("0.002")),
            mapping=mapping,
            metadata=metadata,
            market_price=price,
            evaluated_at=AT,
        )
    with pytest.raises(ExecutionError, match="KRAKEN_PREFLIGHT_BINDING_INVALID"):
        result.validate_against(
            intent=order,
            mapping=mapping,
            metadata=metadata,
            market_price=replace(price, price=price.price + Decimal("1")),
            evaluated_at=AT,
        )


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        ("mapping_pair", "KRAKEN_MAPPING_NOT_AUTHORIZED"),
        ("mapping_aliases", "KRAKEN_MAPPING_NOT_AUTHORIZED"),
        ("metadata_minimum", "KRAKEN_METADATA_NOT_AUTHORIZED"),
        ("metadata_increment", "KRAKEN_METADATA_NOT_AUTHORIZED"),
        ("price_value", "KRAKEN_PRICE_EVIDENCE_INVALID"),
        ("price_observation", "KRAKEN_PRICE_EVIDENCE_INVALID"),
    ],
)
def test_preflight_rechecks_consumed_evidence_integrity(tamper: str, reason: str) -> None:
    mapping, metadata, price = evidence()
    order = intent(order_type=OrderType.MARKET)
    result = KrakenOrderPreflight.build(
        intent=order, mapping=mapping, metadata=metadata, market_price=price, evaluated_at=AT
    )

    if tamper == "mapping_pair":
        object.__setattr__(mapping, "native_identifier", "XETHZEUR")
    elif tamper == "mapping_aliases":
        object.__setattr__(mapping, "native_aliases", ("XETHZEUR",))
    elif tamper == "metadata_minimum":
        object.__setattr__(metadata, "minimum_notional", Decimal("0.01"))
    elif tamper == "metadata_increment":
        object.__setattr__(metadata, "quantity_increment", Decimal("0.00000002"))
    elif tamper == "price_value":
        object.__setattr__(price, "price", price.price * 2)
    else:
        assert price.observation is not None
        object.__setattr__(price.observation, "request_started_at", AT - timedelta(seconds=2))

    with pytest.raises(ExecutionError, match=reason):
        KrakenOrderPreflight.build(
            intent=order, mapping=mapping, metadata=metadata, market_price=price, evaluated_at=AT
        )
    with pytest.raises(ExecutionError, match=reason):
        result.validate_against(
            intent=order, mapping=mapping, metadata=metadata, market_price=price, evaluated_at=AT
        )


def test_limit_preflight_rejects_tampered_metadata_without_market_price() -> None:
    mapping, metadata, _ = evidence()
    order = intent()
    result = KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)
    object.__setattr__(metadata, "minimum_quantity", Decimal("0.00001"))
    with pytest.raises(ExecutionError, match="KRAKEN_METADATA_NOT_AUTHORIZED"):
        KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)
    with pytest.raises(ExecutionError, match="KRAKEN_METADATA_NOT_AUTHORIZED"):
        result.validate_against(intent=order, mapping=mapping, metadata=metadata)


def test_market_price_age_is_bounded_at_the_supplied_evaluation_time() -> None:
    mapping, metadata, price = evidence()
    order = intent(order_type=OrderType.MARKET)
    at_boundary = AT + timedelta(seconds=10)
    result = KrakenOrderPreflight.build(
        intent=order,
        mapping=mapping,
        metadata=metadata,
        market_price=price,
        evaluated_at=at_boundary,
    )
    equivalent_zone = at_boundary.astimezone(timezone(timedelta(hours=2)))
    result.validate_against(
        intent=order,
        mapping=mapping,
        metadata=metadata,
        market_price=price,
        evaluated_at=equivalent_zone,
    )
    result.validate()  # Internal integrity does not by itself establish recency.
    with pytest.raises(ExecutionError, match="KRAKEN_PRICE_EVIDENCE_EXPIRED"):
        result.validate_against(
            intent=order,
            mapping=mapping,
            metadata=metadata,
            market_price=price,
            evaluated_at=AT + timedelta(seconds=11),
        )


@pytest.mark.parametrize(
    ("evaluated_at", "reason"),
    [
        (None, "KRAKEN_PRICE_EVALUATION_TIME_REQUIRED"),
        (AT.replace(tzinfo=None), "KRAKEN_PREFLIGHT_TIME_INVALID"),
        ("2026-09-24T12:00:01Z", "KRAKEN_PREFLIGHT_TIME_INVALID"),
        (AT - timedelta(microseconds=1), "KRAKEN_PRICE_EVIDENCE_EXPIRED"),
        (AT + timedelta(seconds=10, microseconds=1), "KRAKEN_PRICE_EVIDENCE_EXPIRED"),
    ],
)
def test_market_preflight_rejects_missing_invalid_future_or_old_price_time(
    evaluated_at: datetime | None, reason: str
) -> None:
    mapping, metadata, price = evidence()
    with pytest.raises(ExecutionError, match=reason):
        KrakenOrderPreflight.build(
            intent=intent(order_type=OrderType.MARKET),
            mapping=mapping,
            metadata=metadata,
            market_price=price,
            evaluated_at=evaluated_at,
        )


def test_limit_order_without_public_price_needs_no_evaluation_time() -> None:
    mapping, metadata, _ = evidence()
    order = intent()
    result = KrakenOrderPreflight.build(intent=order, mapping=mapping, metadata=metadata)
    result.validate_against(intent=order, mapping=mapping, metadata=metadata)
