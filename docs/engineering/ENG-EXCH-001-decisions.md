# ENG-EXCH-001 — Binance Spot Testnet adapter

Base: `c6d603537012ab82b5c87ecb0d92ed18e16a64f7`.
Authority: CTO Exchange V1 decision and the subsequent fake-only Risk TEST contract.
SPEC-EXCH-001 remains absent; this document records implementation of those decisions.

## Authority and activation

The adapter transports explicit upstream intent; it does not produce a Risk approval,
size a position, create a simulated fill or change Accounting. Risk and OPS are unchanged.
Both currently block TESTNET. Production composition therefore cannot submit an order,
load credentials or check Binance connectivity. LIVE is always blocked.
`authorize_testnet()` accepts only a validated TESTNET READY OPS result with its qualification
and observability references. Current OPS cannot produce such a result.
An authorization record alone never suffices: submission checks it against actual OPS evidence.
Future activation requires a separate review aligning Risk, OPS and Exchange contracts.

The sole environment exception resides in `tests/exchange_support.py`: a test subclass
validates real Risk(TEST) evidence, an explicit fixture authorization and the exact local
FakeTransport class. There is no runtime test flag or environment remapping. HTTP transport
independently verifies OPS before any credential/network work and rejects that fixture path.
A contract test executes real DATA -> Strategy -> Risk(TEST) -> external quantity -> fake Exchange.
It also proves that the same evidence is refused by strict runtime composition.

## Contracts and binding

`UpstreamOrderProof` is an explicit external intent/quantity fact, not an OMS engine.
Its identity covers symbol, side, quantity, Risk/Strategy references, environment and creation time.
`ExchangeOrderRequest` must exactly match the projection of this proof. BUY requires LONG_ENTRY;
SELL requires EXIT. Risk identity, derived decision ID, Strategy evaluation/signal/provenance,
market symbol/environment and policy identity are inspected before dispatch.
All quantities are positive finite Decimal values; no default, rounding or inferred sizing.
Client IDs are `atp-` plus the first 32 SHA-256 hex characters of upstream identity (36 characters).
This is a deterministic 128-bit identifier, with ordinary cryptographic collision limitations.
Same-local-ID/different-request collisions are blocked. No persistent idempotence is claimed.

`ATP_EXCHANGE_TESTNET_V1 / 1.0` is locked at construction and revalidated at each adapter operation.
Public submission/reconciliation reject malformed external evidence without echoing it.
Blocked preflight results deliberately omit untrusted order fields. No arbitrary repr/str is used.
Connectivity has a separate result and grants no readiness or trading authority.

## Transport and credentials

The stdlib HTTP transport fixes `testnet.binance.vision` and allows only
`POST /api/v3/order`, `GET /api/v3/order` and `GET /api/v3/ping` internally.
No public base URL, endpoint override, Live switch, redirects or environment proxy configuration.
TLS verification uses the standard trusted context. No new package dependency.
The injected environment provider reads only `ATP_BINANCE_TESTNET_API_KEY` and
`ATP_BINANCE_TESTNET_API_SECRET`, immediately before dispatch. Credentials stay in transient
transport material with redacted repr, outside domain records, hashes and results.
No diagnostic logs are emitted in V1; no observability event taxonomy extension.
No genuine credentials are used in fixtures. Local wire tests use visibly dummy material.

Protocol source checked: [Binance official Spot Testnet REST API](https://developers.binance.com/en/docs/products/spot/testnet/rest-api).
HMAC-SHA256, `X-MBX-APIKEY`, signed form/query, MARKET quantity, `newClientOrderId`,
`origClientOrderId`, millisecond timestamp and RESULT response follow that protocol.
Dispatch time is explicitly injected; `recvWindow=5000` follows the protocol default.
Socket timeout is 10 seconds; no system clock, remote clock synchronization or background retry.
Responses are bounded to 1 MB; malformed/oversized responses cannot become an acceptance.
Only required response fields are projected; raw body/headers/signature never enter a domain result.

## Uncertainty and local idempotence

A lock serializes one adapter instance and reserves the client ID before dispatch.
A known certain result is returned unchanged for duplicate submission after revalidating input.
Otherwise duplicates query by the same client ID; they never blindly POST again.
A connect failure before request transmission is BLOCKED/TRANSPORT_UNAVAILABLE.
Once transmission is possible, timeout/network failure is UNCERTAIN/SUBMISSION_OUTCOME_UNKNOWN.
Binance -1006/-1007 and 5xx also stay UNCERTAIN. Explicit certain negative 4xx responses are REJECTED.
Malformed successful submission is UNCERTAIN; malformed read-only reconciliation is BLOCKED,
without erasing the original uncertainty or enabling another POST.

A matching query result is ACCEPTED. Query failure/not-found remains UNCERTAIN: an asynchronous
not-found response is not sufficient proof that an order never executed. V1 does not exercise
the optional permission for one controlled resubmission. No automatic retries, including GET;
callers explicitly request another reconciliation. No polling or cancellation engine.
ACCEPTED denotes confirmed exchange reception, not an ATP fill or completed economic lifecycle.

## Validation and deferrals

Unit/contract tests use local fakes with sockets blocked. HTTP serialization, HMAC and error paths
are exercised through a local connection double; no test calls Binance.
Tests cover BUY/SELL, real Risk proof, OPS blocking, runtime type/tamper checks, exact policy,
quantity, deterministic IDs, duplicates, uncertainty, reconciliation, credential redaction,
fixed host, no withdrawal/transfer methods, authority imports and connectivity separation.
No Risk/OPS/Qualification policy change; no sizing, balances, portfolio synchronization,
withdrawal, Margin/Futures, Live, persistence, user stream, worker, OMS lifecycle or AI.
Real Testnet integration and durable deduplication remain separate future missions.
