# ENG-OMS-KRAKEN-004 — Offline spendable-EUR and fee producers

Status: **offline producer semantics qualified; runtime sources remain closed**.

Base: 56fc3fef9e4694f204dbf987a3970c0c0042356c.

## Scope

This mission qualifies deterministic, offline transformation of supplied
Kraken-shaped payloads into the evidence contracts required by the accepted
BTC/EUR entry-exposure policy.

### Spendable EUR

A supplied BalanceEx payload is interpreted with the accepted formula:

available = balance + credit - credit_used - hold_trade

The producer emits SpendableEurEvidence only when one complete EUR row is
present and all required credit and hold facts are explicit.

### Bounded fee

A supplied TradeVolume payload must contain the exact BTC/EUR native pair
XXBTZEUR in both taker and maker schedules. For BUY LIMIT candidates ATP uses
the documented taker maxfee percentage as the conservative bound because a
limit order may execute immediately as taker.

maximum_fee_eur = quantity × limit_price × taker_maxfee_percent / 100

The parser also requires the maker maximum not to exceed the taker maximum.

## Qualification layers

The source contract now distinguishes:

- offline_producer_qualified: parser/transformation semantics are tested;
- observation_qualified: the real observation path has been qualified;
- exposure_source_qualified: the source may participate in a runtime OMS PASS;
- network_authority_granted: this contract itself grants no network authority.

BalanceEx and TradeVolume are offline-producer qualified only. They remain
observation-unqualified and exposure-source-unqualified.

## Authority boundary

This mission does **not** add either route to KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST:

- /0/private/BalanceEx
- /0/private/TradeVolume

No credentials, nonce, signature, HTTP call, economic call, or Live capability
is added.

REAL_ECONOMIC_CALLS = 0.
LIVE = LIVE_FORBIDDEN.
