"""Offline Kraken Spot order preflight.

Transforms an already-authorized ATP OrderIntent into a deterministic, unsigned
AddOrder payload description. No credentials, nonce, signing, HTTP or socket
authority exists in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from atp.exchange.contracts import (
    PublicInstrumentMetadata,
    PublicPriceEvidence,
    VenueId,
    VenueInstrumentMappingEvidence,
)
from atp.exchange.execution import ExecutionError, OrderIntent, OrderType
from atp.exchange.read_only import verify_record
from atp.shared.identity import ContentIdentity

MAX_PUBLIC_PRICE_AGE = timedelta(seconds=10)
MAX_MAPPING_METADATA_AGE = timedelta(minutes=1)


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
        evaluated_at: datetime | None = None,
    ) -> KrakenOrderPreflight:
        if type(intent) is not OrderIntent:
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        intent.validate()
        _validate_evidence(intent, mapping, metadata, market_price, evaluated_at)
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
        """Check structure and content integrity, without granting execution authority."""
        identities: tuple[ContentIdentity, ...] = (
            self.intent_identity,
            self.mapping_identity,
            self.metadata_identity,
            self.content_identity,
        )
        if self.price_evidence_identity is not None:
            identities += (self.price_evidence_identity,)
        if any(
            type(value) is not ContentIdentity
            or value.algorithm != "sha256"
            or type(value.digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value.digest) is None
            for value in identities
        ):
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        if (
            type(self.client_order_id) is not str
            or self.client_order_id != f"atp-{self.intent_identity.digest[:14]}"
            or type(self.payload) is not tuple
            or any(
                type(item) is not tuple
                or len(item) != 2
                or any(type(part) is not str for part in item)
                for item in self.payload
            )
        ):
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        keys = tuple(key for key, _ in self.payload)
        values = dict(self.payload)
        required = {"cl_ord_id", "ordertype", "pair", "type", "volume"}
        if values.get("ordertype") == "limit":
            required.add("price")
        if (
            set(keys) != required
            or len(keys) != len(set(keys))
            or self.payload != tuple(sorted(self.payload))
            or values.get("cl_ord_id") != self.client_order_id
            or values.get("ordertype") not in {"market", "limit"}
            or values.get("type") not in {"buy", "sell"}
            or not values.get("pair")
            or (values.get("ordertype") == "market" and self.price_evidence_identity is None)
        ):
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        for key in ("volume", "price"):
            if key not in values:
                continue
            try:
                number = Decimal(values[key])
            except InvalidOperation:
                raise ExecutionError("KRAKEN_PREFLIGHT_INVALID") from None
            if not number.is_finite() or number <= 0 or _decimal_text(number) != values[key]:
                raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")
        expected = ContentIdentity.from_canonical(
            {
                "client_order_id": self.client_order_id,
                "intent_identity": str(self.intent_identity),
                "mapping_identity": str(self.mapping_identity),
                "metadata_identity": str(self.metadata_identity),
                "payload": self.payload,
                "price_evidence_identity": (
                    None
                    if self.price_evidence_identity is None
                    else str(self.price_evidence_identity)
                ),
            }
        )
        if self.content_identity != expected:
            raise ExecutionError("KRAKEN_PREFLIGHT_INVALID")

    def validate_against(
        self,
        *,
        intent: OrderIntent,
        mapping: VenueInstrumentMappingEvidence,
        metadata: PublicInstrumentMetadata,
        market_price: PublicPriceEvidence | None = None,
        evaluated_at: datetime | None = None,
    ) -> None:
        """Rebuild against caller-supplied evidence; a digest alone is not authority.

        Evidence age is checked against the caller-supplied evaluation time.
        This method does not authenticate that time or authorize submission.
        """
        self.validate()
        expected = KrakenOrderPreflight.build(
            intent=intent,
            mapping=mapping,
            metadata=metadata,
            market_price=market_price,
            evaluated_at=evaluated_at,
        )
        if self != expected:
            raise ExecutionError("KRAKEN_PREFLIGHT_BINDING_INVALID")


def _validate_evidence(
    intent: OrderIntent,
    mapping: VenueInstrumentMappingEvidence,
    metadata: PublicInstrumentMetadata,
    market_price: PublicPriceEvidence | None,
    evaluated_at: datetime | None,
) -> None:
    if evaluated_at is not None and (
        type(evaluated_at) is not datetime
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ExecutionError("KRAKEN_PREFLIGHT_TIME_INVALID")
    if (
        type(mapping) is not VenueInstrumentMappingEvidence
        or not verify_record(mapping, VenueInstrumentMappingEvidence)
        or mapping.venue is not VenueId.KRAKEN
        or mapping.instrument != intent.instrument
        or mapping.instrument_status != "online"
    ):
        raise ExecutionError("KRAKEN_MAPPING_NOT_AUTHORIZED")
    if (
        type(metadata) is not PublicInstrumentMetadata
        or not verify_record(metadata, PublicInstrumentMetadata)
        or metadata.venue is not VenueId.KRAKEN
        or metadata.instrument != intent.instrument
        or metadata.mapping_identity != mapping.content_identity
        or metadata.status != "online"
        or metadata.observed_at != mapping.observed_at
    ):
        raise ExecutionError("KRAKEN_METADATA_NOT_AUTHORIZED")
    if market_price is None and intent.order_type is OrderType.MARKET:
        raise ExecutionError("KRAKEN_MARKET_PRICE_REQUIRED")
    if evaluated_at is None:
        raise ExecutionError("KRAKEN_PREFLIGHT_TIME_REQUIRED")
    metadata_age = evaluated_at.astimezone(UTC) - mapping.observed_at.astimezone(UTC)
    if not timedelta(0) <= metadata_age <= MAX_MAPPING_METADATA_AGE:
        raise ExecutionError("KRAKEN_METADATA_EVIDENCE_EXPIRED")
    if market_price is None:
        return
    if (
        type(market_price) is not PublicPriceEvidence
        or not verify_record(market_price, PublicPriceEvidence)
        or market_price.venue is not VenueId.KRAKEN
        or market_price.instrument != intent.instrument
        or market_price.mapping_identity != mapping.content_identity
        or market_price.freshness != "OBSERVATION_FRESH"
    ):
        raise ExecutionError("KRAKEN_PRICE_EVIDENCE_INVALID")
    age = evaluated_at.astimezone(UTC) - market_price.observed_at.astimezone(UTC)
    if not timedelta(0) <= age <= MAX_PUBLIC_PRICE_AGE:
        raise ExecutionError("KRAKEN_PRICE_EVIDENCE_EXPIRED")


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
