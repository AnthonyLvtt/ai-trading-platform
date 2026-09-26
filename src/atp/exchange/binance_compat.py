"""Compatibility helpers around existing Binance records.

The legacy Binance model is intentionally not rewritten: its content identities are an
accepted contract used by the controlled first-order campaign.
"""

from dataclasses import dataclass

from atp.exchange.contracts import BTC_USDT, CanonicalInstrumentId, VenueId
from atp.exchange.model import ExchangePolicy
from atp.exchange.read_only import EvidenceError
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class BinanceCompatibility:
    venue: VenueId = VenueId.BINANCE_SPOT

    @property
    def policy_identity(self) -> ContentIdentity:
        return ExchangePolicy().content_identity

    def native_symbol(self, instrument: CanonicalInstrumentId) -> str:
        if instrument != BTC_USDT:
            raise EvidenceError("UNSUPPORTED_BINANCE_INSTRUMENT")
        return "BTCUSDT"
