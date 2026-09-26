"""Exchange-neutral public Spot contracts.

These records carry no credentials and grant no execution authority. Existing Binance
execution records deliberately remain in :mod:`atp.exchange.model` so their serialized
identities are unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.identity import ContentIdentity


class VenueId(StrEnum):
    BINANCE_SPOT = "BINANCE_SPOT"
    KRAKEN_SPOT = "KRAKEN_SPOT"


class MarketKind(StrEnum):
    SPOT = "SPOT"


@dataclass(frozen=True, slots=True)
class CanonicalInstrumentId(EvidenceRecord):
    base_asset: str
    quote_asset: str
    market: MarketKind = MarketKind.SPOT

    def __post_init__(self) -> None:
        if (
            type(self.base_asset) is not str
            or type(self.quote_asset) is not str
            or not re.fullmatch(r"[A-Z0-9]{2,12}", self.base_asset)
            or not re.fullmatch(r"[A-Z0-9]{2,12}", self.quote_asset)
            or self.base_asset == self.quote_asset
            or self.market is not MarketKind.SPOT
        ):
            raise EvidenceError("INVALID_CANONICAL_INSTRUMENT")
        EvidenceRecord.__post_init__(self)

    @property
    def symbol(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"


BTC_EUR = CanonicalInstrumentId("BTC", "EUR")
BTC_USDT = CanonicalInstrumentId("BTC", "USDT")


@dataclass(frozen=True, slots=True)
class VenueInstrumentMappingEvidence(EvidenceRecord):
    venue: VenueId
    instrument: CanonicalInstrumentId
    native_identifier: str
    native_aliases: tuple[str, ...]
    metadata_identity: ContentIdentity

    def __post_init__(self) -> None:
        if (
            type(self.venue) is not VenueId
            or type(self.instrument) is not CanonicalInstrumentId
            or type(self.native_identifier) is not str
            or not self.native_identifier
            or type(self.native_aliases) is not tuple
            or not self.native_aliases
            or any(type(alias) is not str or not alias for alias in self.native_aliases)
            or len(set(self.native_aliases)) != len(self.native_aliases)
            or type(self.metadata_identity) is not ContentIdentity
        ):
            raise EvidenceError("INVALID_INSTRUMENT_MAPPING")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class PublicServerTimeEvidence(EvidenceRecord):
    venue: VenueId
    server_time: datetime
    observed_at: datetime
    source_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class PublicSystemStatusEvidence(EvidenceRecord):
    venue: VenueId
    status: str
    effective_at: datetime
    observed_at: datetime
    source_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class PublicInstrumentMetadata(EvidenceRecord):
    venue: VenueId
    instrument: CanonicalInstrumentId
    mapping_identity: ContentIdentity
    status: str
    price_increment: Decimal
    quantity_increment: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    observed_at: datetime
    source_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class PublicCandle(EvidenceRecord):
    open_time: datetime
    interval_minutes: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int


@dataclass(frozen=True, slots=True)
class PublicCandleEvidence(EvidenceRecord):
    venue: VenueId
    instrument: CanonicalInstrumentId
    mapping_identity: ContentIdentity
    candles: tuple[PublicCandle, ...]
    observed_at: datetime
    source_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class PublicPriceEvidence(EvidenceRecord):
    venue: VenueId
    instrument: CanonicalInstrumentId
    mapping_identity: ContentIdentity
    price: Decimal
    source: str
    observed_at: datetime
    source_identity: ContentIdentity


class PublicExchangePort(Protocol):
    venue: VenueId

    def server_time(self, observed_at: datetime) -> PublicServerTimeEvidence: ...

    def system_status(self, observed_at: datetime) -> PublicSystemStatusEvidence: ...

    def instrument_metadata(
        self, instrument: CanonicalInstrumentId, observed_at: datetime
    ) -> tuple[VenueInstrumentMappingEvidence, PublicInstrumentMetadata]: ...

    def closed_candles(
        self,
        instrument: CanonicalInstrumentId,
        mapping: VenueInstrumentMappingEvidence,
        interval_minutes: int,
        observed_at: datetime,
    ) -> PublicCandleEvidence: ...

    def price(
        self,
        instrument: CanonicalInstrumentId,
        mapping: VenueInstrumentMappingEvidence,
        observed_at: datetime,
    ) -> PublicPriceEvidence: ...


class VenueSelectionError(EvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class SelectedPublicExchange:
    """Binds exactly one explicit venue to one public port; there is no fallback list."""

    venue: VenueId
    port: PublicExchangePort

    def __post_init__(self) -> None:
        if type(self.venue) is not VenueId or self.port.venue is not self.venue:
            raise VenueSelectionError("VENUE_MISMATCH")

    def require_mapping(
        self,
        instrument: CanonicalInstrumentId,
        mapping: VenueInstrumentMappingEvidence,
    ) -> None:
        if mapping.venue is not self.venue or mapping.instrument != instrument:
            raise VenueSelectionError("FOREIGN_MAPPING")
