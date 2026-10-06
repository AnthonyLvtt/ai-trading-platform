# ENG-EXCH-KRAKEN-017 — Global ledger history gate

## Base and motivation

Base: `a4472202702bfe2b47d78eb4aa2264b3a63e8e5f`.
Post-merge Quality #168 succeeded on this exact base.

The offline ledger previously validated only the requested order's history.
A malformed transition or key for a different order could remain in the same
database while new orders were prepared. A safety decision made from that
partially corrupted ledger should fail closed.

## Scope

- Walk every stored transition in sequence, checking canonical key format,
  predecessor, allowed next state, and reason-code shape per order.
- Return the requested order's state only after the complete database passes.
- Keep the audit inside the existing `BEGIN IMMEDIATE` write transaction
  before any new transition is appended.
- Test unrelated malformed keys and transitions, and valid interleaved
  histories for two orders.

## Boundary

The full scan is linear in the number of stored transitions on each ledger
read or write. It detects malformed retained rows, not deletion or replacement
with a self-consistent forged history. The SQLite file is not tamper-proof and
the ledger does not prove a Kraken outcome or authorize submission.

No credential, nonce, signature, HTTP/socket transport, retry, economic call,
or Live capability is added. Kraken-only, Spot BTC/EUR, offline and
fail-closed. `REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
