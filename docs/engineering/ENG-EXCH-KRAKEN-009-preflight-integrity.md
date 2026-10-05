# ENG-EXCH-KRAKEN-009 — Offline preflight integrity and source binding

## Base and motivation

Base: `dbbab8ac524c7c1e71aebb6df047e6b058e701aa`.
Post-merge Quality #152 succeeded on this exact base.

ENG-EXCH-KRAKEN-008 produced a content identity but its validator did not compare
that identity with the stored fields. Valid-looking changes to volume, price,
side, pair or evidence identities could therefore pass validation.

## Scope

- Recompute and compare the canonical content identity during validation.
- Validate identity types/digests and the deterministic client ID binding.
- Require an immutable, sorted, unique payload with exactly the allowed fields.
- Require positive finite canonical decimal volume and limit price.
- Require a price evidence identity for market orders.
- Add `validate_against` to rebuild and compare against the caller's intent,
  mapping, metadata and optional price evidence, rerunning existing constraints.
- Cover field tampering, malformed payloads, recomputed digests, valid BUY/SELL
  market/limit orders, changed evidence and the disabled submission boundary.

## Trust boundary and limits

A content hash detects inconsistency; it does not authenticate evidence. A caller
that replaces both fields and digest can construct an internally consistent
record. Consumers needing source binding must use `validate_against` with their
trusted source objects. This method does not create provenance or independently
qualify those sources. Freshness retains the existing observation semantics; it
is not a present-time freshness check or execution authorization.

No credential, nonce, signature, transport, retry or Live capability is added.
Kraken-only, Spot BTC/EUR. `REAL_ECONOMIC_CALLS = 0`.
`LIVE = LIVE_FORBIDDEN`. Economic submission remains disabled by construction.
