# ENG-EXCH-KRAKEN-013 — Bounded mapping and metadata age in offline preflight

## Base and motivation

Base: `d833269cab66fad0b01ec6c9d09bffc3b0e396fa`.
Post-merge Quality #160 succeeded on this exact base.

The preflight requires an online Kraken BTC/EUR mapping and metadata, but
previously accepted these records regardless of age. A stale online status or
old minimum/increment values could therefore be reused for a market or limit
order. ENG-EXCH-KRAKEN-012 bounded public price age only when a price was
supplied.

## Scope

- Require a timezone-aware caller-supplied `evaluated_at` for every preflight,
  including limit orders without a public price.
- Reject mapping and metadata observed after evaluation or more than one minute
  before it. The one-minute boundary is inclusive. The existing exact shared
  observation-time binding remains required.
- Recheck age during `validate_against`, so an older preflight cannot retain
  an earlier recency decision.
- Preserve the ten-second public price bound from ENG-EXCH-KRAKEN-012.
- Cover limit and market behavior, boundary times, future and expired evidence,
  timezone equivalence, and revalidation in offline tests.

## Boundary

The caller supplies the evaluation time. This check does not authenticate the
clock or prove that Kraken's current instrument status is unchanged. The
one-minute policy bounds only the age of observed public evidence; it grants
no economic execution authority.

No credential, nonce, signature, HTTP/socket transport, retry, economic call,
or Live capability is added. Kraken-only, Spot BTC/EUR, offline and fail-closed.
`REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
