"""Kraken-only public Spot contracts.

These records carry no credentials and grant no execution authority.
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
    KRAKEN = "KRAKEN"


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


@dataclass(frozen=True, slots=True)
class VenueInstrumentMappingEvidence(EvidenceRecord):
    venue: VenueId
    instrument: CanonicalInstrumentId
    native_identifier: str
    native_aliases: tuple[str, ...]
    metadata_identity: ContentIdentity
    native_base_asset: str
    native_quote_asset: str
    instrument_status: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or type(self.instrument) is not CanonicalInstrumentId
            or type(self.native_identifier) is not str
            or not self.native_identifier
            or type(self.native_aliases) is not tuple
            or not self.native_aliases
            or any(type(alias) is not str or not alias for alias in self.native_aliases)
            or len(set(self.native_aliases)) != len(self.native_aliases)
            or any(
                type(v) is not str or not v
                for v in (self.native_base_asset, self.native_quote_asset, self.instrument_status)
            )
            or self.observed_at.tzinfo is None
            or type(self.metadata_identity) is not ContentIdentity
        ):
            raise EvidenceError("INVALID_INSTRUMENT_MAPPING")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class PublicObservation(EvidenceRecord):
    request_started_at: datetime
    response_received_at: datetime

    def __post_init__(self) -> None:
        if (
            self.request_started_at.tzinfo is None
            or self.response_received_at.tzinfo is None
            or not 0 <= (self.response_received_at - self.request_started_at).total_seconds() <= 10
        ):
            raise EvidenceError("PUBLIC_OBSERVATION_UNBOUNDED")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class PublicServerTimeEvidence(EvidenceRecord):
    venue: VenueId
    server_time: datetime
    observed_at: datetime
    source_identity: ContentIdentity
    observation: PublicObservation | None = None


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
    observation: PublicObservation | None = None
    freshness: str = "UNKNOWN"
    event_time: datetime | None = None

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or self.freshness not in {"UNKNOWN", "OBSERVATION_FRESH"}
            or (
                self.freshness == "OBSERVATION_FRESH"
                and (
                    self.observation is None
                    or self.observation.response_received_at != self.observed_at
                )
            )
        ):
            raise EvidenceError("PUBLIC_PRICE_FRESHNESS_INVALID")
        EvidenceRecord.__post_init__(self)


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
    venue: VenueId
    port: PublicExchangePort

    def __post_init__(self) -> None:
        if self.venue is not VenueId.KRAKEN or self.port.venue is not VenueId.KRAKEN:
            raise VenueSelectionError("VENUE_MISMATCH")

    def require_mapping(
        self,
        instrument: CanonicalInstrumentId,
        mapping: VenueInstrumentMappingEvidence,
    ) -> None:
        if mapping.venue is not VenueId.KRAKEN or mapping.instrument != instrument:
            raise VenueSelectionError("FOREIGN_MAPPING")
