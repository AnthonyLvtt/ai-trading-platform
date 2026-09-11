"""Binance Spot Testnet boundary; current Risk/OPS policies prevent runtime activation."""

from atp.exchange.adapter import ExchangeAdapter, authorize_testnet
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
