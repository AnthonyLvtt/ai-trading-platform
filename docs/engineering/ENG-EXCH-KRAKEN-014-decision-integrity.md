# ENG-EXCH-KRAKEN-014 — Decision integrity at order intent creation

## Base and motivation

Base: `a3e42282a594e9b59adbc1ea6cd726848893c87a`.
Post-merge Quality #162 succeeded on this exact base.

The order-intent boundary checked selected Strategy and Risk fields, but did
not revalidate their internal identities. A rejected Risk decision relabelled
as approved, or a Strategy signal relabelled to change the order side, could
pass those selected checks after the frozen objects were mutated.

## Scope

- Revalidate the exact Strategy evaluation and signal, and the exact Risk
  decision, before accepting an order intent.
- Convert malformed or internally inconsistent decision objects into the
  existing fail-closed `RISK_BINDING_INVALID` result.
- Preserve the existing approval, provenance, side, and Spot BTC/EUR checks.
- Cover mutation of Risk status and market context, Strategy evaluation
  status, and Strategy signal kind in offline tests.

## Boundary

Recomputed identities establish internal consistency; they do not authenticate
the source of a self-consistent object. This change grants no economic execution
authority. No credential, nonce, signature, HTTP/socket transport, retry,
economic call, or Live capability is added. Kraken-only, Spot BTC/EUR, offline
and fail-closed. `REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
