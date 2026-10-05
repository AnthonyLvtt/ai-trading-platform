"""Offline Kraken Spot order preflight.

Transforms an already-authorized ATP OrderIntent into a deterministic, unsigned
AddOrder payload description. No credentials, nonce, signing, HTTP or socket
authority exists in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from atp.exchange.contracts import (
    PublicInstrumentMetadata,
    PublicPriceEvidence,
    VenueId,
    VenueInstrumentMappingEvidence,
)
from atp.exchange.execution import ExecutionError, OrderIntent, OrderType
from atp.shared.identity import ContentIdentity


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _aligned(value: Decimal, increment: Decimal) -> bool:
    if not increment.is_finite() or increment <= 0:
        return False
    return value % increment == 0


@dataclass(frozen=True, slots=True, init=False)
class KrakenOrderPreflight:
    intent_identity: ContentIdentity
    mapping_identity: ContentIdentity
    metadata_identity: ContentIdentity
    price_evidence_identity: ContentIdentity | None
    client_order_id: str
    payload: tuple[tuple[str, str], ...]
    content_identity: ContentIdentity

    @classmethod
    def build(
        cls,
        *,
        intent: OrderIntent,
        mapping: VenueInstrumentMappingEvidence,
        metadata: PublicInstrumentMetadata,
        market_price: PublicPriceEvidence | None = None,
    ) -> KrakenOrderPreflight:
        if type(intent) is not OrderIntent:
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        intent.validate()
        _validate_evidence(intent, mapping, metadata, market_price)
        _validate_constraints(intent, metadata, market_price)

        client_order_id = f"atp-{intent.idempotency_key.digest[:14]}"
        payload_items = [
            ("cl_ord_id", client_order_id),
            ("ordertype", intent.order_type.value.casefold()),
            ("pair", mapping.native_identifier),
            ("type", intent.side.value.casefold()),
            ("volume", _decimal_text(intent.quantity)),
        ]
        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            payload_items.append(("price", _decimal_text(intent.limit_price)))
        payload = tuple(sorted(payload_items))

        value = {
            "client_order_id": client_order_id,
            "intent_identity": str(intent.idempotency_key),
            "mapping_identity": str(mapping.content_identity),
            "metadata_identity": str(metadata.content_identity),
            "payload": payload,
            "price_evidence_identity": (
                None if market_price is None else str(market_price.content_identity)
            ),
        }

        result = object.__new__(cls)
        object.__setattr__(result, "intent_identity", intent.idempotency_key)
        object.__setattr__(result, "mapping_identity", mapping.content_identity)
        object.__setattr__(result, "metadata_identity", metadata.content_identity)
        object.__setattr__(
            result,
            "price_evidence_identity",
            None if market_price is None else market_price.content_identity,
        )
        object.__setattr__(result, "client_order_id", client_order_id)
        object.__setattr__(result, "payload", payload)
        object.__setattr__(result, "content_identity", ContentIdentity.from_canonical(value))
        result.validate()
        return result

    def validate(self) -> None:
        if not re.fullmatch(r"atp-[0-9a-f]{14}", self.client_order_id):
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        if len(self.payload) not in {5, 6}:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        keys = tuple(key for key, _ in self.payload)
        required = {"cl_ord_id", "ordertype", "pair", "type", "volume"}
        if not required.issubset(keys) or len(keys) != len(set(keys)):
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        values = dict(self.payload)
        if values["cl_ord_id"] != self.client_order_id:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        if values["ordertype"] not in {"market", "limit"}:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        if values["type"] not in {"buy", "sell"}:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        if values["ordertype"] == "limit" and "price" not in values:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        if values["ordertype"] == "market" and "price" in values:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")


def _validate_evidence(
    intent: OrderIntent,
    mapping: VenueInstrumentMappingEvidence,
    metadata: PublicInstrumentMetadata,
    market_price: PublicPriceEvidence | None,
) -> None:
    if (
        type(mapping) is not VenueInstrumentMappingEvidence
        or mapping.venue is not VenueId.KRAKEN
        or mapping.instrument != intent.instrument
        or mapping.instrument_status != "online"
    ):
        raise ExecutionError("KRAKEN_MAPPING_NOT_AUTHORIZED")
    if (
        type(metadata) is not PublicInstrumentMetadata
        or metadata.venue is not VenueId.KRAKEN
        or metadata.instrument != intent.instrument
        or metadata.mapping_identity != mapping.content_identity
        or metadata.status != "online"
        or metadata.observed_at != mapping.observed_at
    ):
        raise ExecutionError("KRAKEN_METADATA_NOT_AUTHORIZED")
    if market_price is None:
        if intent.order_type is OrderType.MARKET:
            raise ExecutionError("KRAKEN_MARKET_PRICE_REQUIRED")
        return
    if (
        type(market_price) is not PublicPriceEvidence
        or market_price.venue is not VenueId.KRAKEN
        or market_price.instrument != intent.instrument
        or market_price.mapping_identity != mapping.content_identity
        or market_price.freshness != "OBSERVATION_FRESH"
    ):
        raise ExecutionError("KRAKEN_PRICE_EVIDENCE_INVALID")


def _validate_constraints(
    intent: OrderIntent,
    metadata: PublicInstrumentMetadata,
    market_price: PublicPriceEvidence | None,
) -> None:
    if (
        not metadata.quantity_increment.is_finite()
        or metadata.quantity_increment <= 0
        or not metadata.price_increment.is_finite()
        or metadata.price_increment <= 0
        or not metadata.minimum_quantity.is_finite()
        or metadata.minimum_quantity <= 0
        or not metadata.minimum_notional.is_finite()
        or metadata.minimum_notional <= 0
    ):
        raise ExecutionError("KRAKEN_METADATA_CONSTRAINT_INVALID")
    if intent.quantity < metadata.minimum_quantity:
        raise ExecutionError("KRAKEN_QUANTITY_BELOW_MINIMUM")
    if not _aligned(intent.quantity, metadata.quantity_increment):
        raise ExecutionError("KRAKEN_QUANTITY_INCREMENT_INVALID")

    if intent.order_type is OrderType.LIMIT:
        assert intent.limit_price is not None
        effective_price = intent.limit_price
        if not _aligned(effective_price, metadata.price_increment):
            raise ExecutionError("KRAKEN_PRICE_INCREMENT_INVALID")
    else:
        assert market_price is not None
        effective_price = market_price.price

    if intent.quantity * effective_price < metadata.minimum_notional:
        raise ExecutionError("KRAKEN_NOTIONAL_BELOW_MINIMUM")
