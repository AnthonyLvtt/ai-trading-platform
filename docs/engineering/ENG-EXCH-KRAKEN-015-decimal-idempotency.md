# ENG-EXCH-KRAKEN-015 — Canonical decimal order identity

## Base and motivation

Base: `67c8bdd1f1cd13a2d6eb929672981446a137d970`.
Post-merge Quality #164 succeeded on this exact base.

Order intent identity previously hashed `str(Decimal)` while the unsigned
Kraken payload used fixed-point decimal text without trailing fractional
zeroes. Therefore numerically identical orders such as `0.001` and `0.0010`
could have distinct idempotency keys and client order IDs despite identical
payloads. The ledger could record them as separate preparations.

## Scope

- Use one fixed-point decimal representation for quantity and optional limit
  price in both order identity and Kraken payload construction.
- Revalidate existing intents against that same representation.
- Prove that market and limit orders with equivalent decimal spellings share an
  idempotency key, that the ledger rejects a second preparation, and that
  preflight payload, client order ID, and content identity remain equal.

## Boundary

This changes the identity of an order created from a noncanonical decimal
spelling relative to earlier builds. It does not rewrite existing ledger rows;
operators must not treat an old row and a new row as proof of two economic
submissions. No economic submission implementation exists.

The representation rule does not authenticate the source of a decision or
public evidence. No credential, nonce, signature, HTTP/socket transport,
retry, economic call, or Live capability is added. Kraken-only, Spot BTC/EUR,
offline and fail-closed. `REAL_ECONOMIC_CALLS = 0`.
`LIVE = LIVE_FORBIDDEN`.
