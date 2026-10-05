# ENG-EXCH-KRAKEN-010 — Atomic offline execution ledger transitions

## Base and motivation

Base: `de8cfd132e463b16fc30756ab74e27d6f8f185b4`.
Quality post-merge #154 succeeded on this exact base.

The ledger previously read the current state before opening its write transaction.
Two callers could both observe `ATTEMPT_STARTED` and append the mutually exclusive
`ACKNOWLEDGED` and `UNKNOWN` outcomes. The per-state uniqueness constraint did not
prevent this fork because the states differ.

## Scope

- Read and validate the existing transition chain under `BEGIN IMMEDIATE` before
  checking and appending a new transition.
- Reject an unexpected starting state, invalid predecessor, unknown state, or
  malformed reason code in persisted history.
- Keep duplicate preparation and invalid transitions fail-closed.
- Reproduce competing outcomes and corrupted history with offline SQLite tests.

## Boundary

This ledger is an append-only local record, not a proof against a caller with
write access to the SQLite file. It does not authorize submission, perform
reconciliation, or prove any Kraken exchange outcome. No credential, signature,
network transport, retry, or Live capability is added. Kraken-only, Spot BTC/EUR.
`REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
