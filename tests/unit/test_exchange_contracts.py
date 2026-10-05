from datetime import UTC, datetime

import pytest

from atp.exchange.contracts import (
    BTC_EUR,
    CanonicalInstrumentId,
    SelectedPublicExchange,
    VenueId,
    VenueInstrumentMappingEvidence,
    VenueSelectionError,
)
from atp.shared.identity import ContentIdentity


class Port:
    def __init__(self, venue: object) -> None:
        self.venue = venue


def test_explicit_kraken_selection_and_foreign_instrument_fail_closed() -> None:
    with pytest.raises(VenueSelectionError, match="VENUE_MISMATCH"):
        SelectedPublicExchange(VenueId.KRAKEN, Port("OTHER"))  # type: ignore[arg-type]

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

    eth_eur = CanonicalInstrumentId("ETH", "EUR")
    with pytest.raises(VenueSelectionError, match="FOREIGN_MAPPING"):
        selected.require_mapping(eth_eur, mapping)
