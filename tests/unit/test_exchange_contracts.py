from dataclasses import replace
from datetime import UTC, datetime

import pytest

from atp.exchange.binance_compat import BinanceCompatibility
from atp.exchange.contracts import (
    BTC_EUR,
    BTC_USDT,
    SelectedPublicExchange,
    VenueId,
    VenueSelectionError,
)
from atp.exchange.model import ExchangePolicy
from atp.exchange.read_only import EvidenceError
from atp.shared.identity import ContentIdentity


class Port:
    def __init__(self, venue: VenueId) -> None:
        self.venue = venue


def test_binance_compatibility_preserves_existing_policy_identity() -> None:
    compatibility = BinanceCompatibility()
    assert compatibility.native_symbol(BTC_USDT) == "BTCUSDT"
    assert str(compatibility.policy_identity) == (
        "sha256:8c8c828ea009881aa70916bbd64bd46be6dceea5eae371ccafc789cb817616f2"
    )
    assert compatibility.policy_identity == ExchangePolicy().content_identity
    with pytest.raises(EvidenceError, match="UNSUPPORTED_BINANCE_INSTRUMENT"):
        compatibility.native_symbol(BTC_EUR)


def test_explicit_venue_selection_and_foreign_mapping_fail_closed() -> None:
    from atp.exchange.contracts import VenueInstrumentMappingEvidence

    with pytest.raises(VenueSelectionError, match="VENUE_MISMATCH"):
        SelectedPublicExchange(VenueId.KRAKEN, Port(VenueId.BINANCE))  # type: ignore[arg-type]
    selected = SelectedPublicExchange(VenueId.KRAKEN, Port(VenueId.KRAKEN))  # type: ignore[arg-type]
    mapping = VenueInstrumentMappingEvidence(
        VenueId.KRAKEN,
        BTC_EUR,
        "XXBTZEUR",
        ("XXBTZEUR", "XBTEUR", "XBT/EUR"),
        ContentIdentity.from_text("metadata"),
        "XXBT",
        "ZEUR",
        "online",
        datetime(2026, 9, 24, tzinfo=UTC),
    )
    selected.require_mapping(BTC_EUR, mapping)
    with pytest.raises(VenueSelectionError, match="FOREIGN_MAPPING"):
        selected.require_mapping(BTC_USDT, mapping)
    foreign = replace(mapping, venue=VenueId.BINANCE)
    with pytest.raises(VenueSelectionError, match="FOREIGN_MAPPING"):
        selected.require_mapping(BTC_EUR, foreign)
