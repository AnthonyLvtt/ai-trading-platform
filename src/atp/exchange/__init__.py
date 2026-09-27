"""Exchange boundaries. Binance execution identities remain backward compatible."""

from atp.exchange.adapter import ExchangeAdapter, authorize_testnet
from atp.exchange.binance_compat import BinanceCompatibility
from atp.exchange.contracts import (
    BTC_EUR,
    BTC_USDT,
    CanonicalInstrumentId,
    MarketKind,
    PublicExchangePort,
    SelectedPublicExchange,
    VenueId,
    VenueInstrumentMappingEvidence,
    VenueSelectionError,
)
from atp.exchange.model import (
    ExchangeConnectivityResult,
    ExchangeOrderRequest,
    ExchangePolicy,
    ExchangeSubmissionResult,
    Reason,
    Side,
    Status,
    TestnetExecutionAuthorization,
    UpstreamOrderProof,
    client_order_id,
    request_from_proof,
)
from atp.exchange.transport import (
    BinanceTestnetHTTPTransport,
    EnvironmentCredentialsProvider,
    ExchangeCredentialsProvider,
    ExchangeTransport,
)

__all__ = [
    "ExchangeConnectivityResult",
    "ExchangeAdapter",
    "BinanceCompatibility",
    "BTC_EUR",
    "BTC_USDT",
    "CanonicalInstrumentId",
    "MarketKind",
    "PublicExchangePort",
    "SelectedPublicExchange",
    "VenueId",
    "VenueInstrumentMappingEvidence",
    "VenueSelectionError",
    "authorize_testnet",
    "ExchangeOrderRequest",
    "ExchangePolicy",
    "ExchangeSubmissionResult",
    "Reason",
    "Side",
    "Status",
    "TestnetExecutionAuthorization",
    "UpstreamOrderProof",
    "client_order_id",
    "request_from_proof",
    "BinanceTestnetHTTPTransport",
    "EnvironmentCredentialsProvider",
    "ExchangeCredentialsProvider",
    "ExchangeTransport",
]
