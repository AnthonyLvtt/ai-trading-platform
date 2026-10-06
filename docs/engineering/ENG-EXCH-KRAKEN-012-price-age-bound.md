# ENG-EXCH-KRAKEN-012 — Bounded public price age in offline preflight

## Base and motivation

Base: `4cffb7424431784e0676cae774b597c7a4da35e5`.
Post-merge Quality #158 succeeded on this exact base.

`OBSERVATION_FRESH` says that the public ticker price is bound to a bounded
request/response observation. It does not say how old that observation is when
order preflight uses it. A days-old record could still pass the market-order
minimum-notional check.

## Scope

- Require a timezone-aware caller-supplied `evaluated_at` whenever a public
  price is consumed by preflight, including market orders.
- Reject a price observed after `evaluated_at` or more than ten seconds before it.
  The ten-second boundary is inclusive.
- Recheck age at each `validate_against` call, so an earlier passing preflight
  does not keep its earlier recency result.
- Keep limit orders with no public price usable without an evaluation time.
- Exercise the boundary, missing/naive times, future prices, elapsed time and
  equivalent timezone offsets in offline tests.

## Boundary

`evaluated_at` is supplied by the caller. This check does not authenticate the
clock, prove the age of Kraken's last trade in the ticker, or grant execution
authority. A limit order with no public price still relies on its limit price
for the notional check; metadata age is outside this mission.

No credential, nonce, signature, HTTP/socket transport, retry, economic call,
or Live capability is added. Kraken-only, Spot BTC/EUR, offline and fail-closed.
`REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
