# ENG-EXCH-KRAKEN-011 — Verify public evidence at preflight consumption

## Base and motivation

Base: `1610f1bb4f5f18c7ea399d572a5557fb077d9acf`.
Post-merge Quality #156 succeeded on this exact base.

The Kraken order preflight checked evidence identity bindings and selected
fields, but did not recompute the public evidence records' content identities.
A frozen record can still be changed through `object.__setattr__` after creation.
Its original identity would then remain while the native pair, metadata limits,
or market price used by preflight changed.

## Scope

- Verify complete mapping, metadata and supplied price records, including nested
  observations, before reading their fields or constraints.
- Apply the same checks when a stored preflight is revalidated against supplied
  source records.
- Cover tampered native mapping, metadata limits and increments, market price,
  and observation data for market and limit orders.

## Boundary

An internally consistent record can still be fabricated by a caller. Record
integrity is not exchange authentication or present-time freshness. The source
selection and trust decision remain outside this preflight.

No credential, nonce, signature, network transport, retry, economic call, or
Live capability is added. Kraken-only, Spot BTC/EUR, offline and fail-closed.
`REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
