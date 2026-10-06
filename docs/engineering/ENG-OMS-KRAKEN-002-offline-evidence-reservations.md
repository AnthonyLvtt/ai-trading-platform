# ENG-OMS-KRAKEN-002 — Offline evidence shapes and atomic reservations

Status: **offline primitives only; runtime PASS not qualified**.

Following the CTO acceptance of `ENG-OMS-KRAKEN-001`, this mission adds three
content-identified shapes at the OMS boundary: `SpendableEurEvidence` binds an
explicit EUR amount to an account, credential reference, balance source, time,
and qualification identity; `BoundedFeeEvidence` binds a maximum EUR fee to the
same account and an exact candidate; `FreshnessEvidence` binds an observation
to an assessment time and an explicit policy identity and maximum age. These
types validate internal consistency, not the authenticity or approval of their
sources. There is no trusted qualification registry, accepted private freshness
limit, fee source, or runtime assessment consuming them.

`ExposureReservationLedger` provides a SQLite `BEGIN IMMEDIATE` check-and-insert
for the same account budget. It sums all previously reserved amounts for that
account across snapshots using exact decimal arithmetic, then records a unique
candidate only when the new sum fits the supplied budget. Concurrent candidates
cannot both consume the same remaining amount. Duplicate candidate identities
block, and reservations have no automatic expiry or release. A separate
qualified reconciliation policy is required before safe release; a crash leaves
the budget conservatively reserved. The caller-supplied budget is **not**
qualified by this primitive; no path from these contracts to order submission
or an exposure PASS exists.

The accepted BUY LIMIT rule remains `quantity × limit_price + bounded_fee <=
min(10% × portfolio_value_eur, spendable_EUR)`. Missing, stale, contradictory,
or unbound evidence must block when an assessment is implemented. BUY MARKET
and SELL/EXIT need separate policies as specified in `ENG-OMS-KRAKEN-001`.

Kraken-only, Spot BTC/EUR, offline/fail-closed. `REAL_ECONOMIC_CALLS = 0`;
`LIVE = LIVE_FORBIDDEN`.
