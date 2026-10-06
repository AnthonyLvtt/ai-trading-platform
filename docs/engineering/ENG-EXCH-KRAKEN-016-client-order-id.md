# ENG-EXCH-KRAKEN-016 — Wider deterministic client order ID

## Base and motivation

Base: `bb5e48d2ce9c3b1f3aacc432f5406167a7949056`.
Post-merge Quality #166 succeeded on this exact base.

The unsigned preflight previously used only 14 hexadecimal characters of
the 256-bit intent key in `cl_ord_id`. Two distinct keys with the same prefix
would therefore receive the same client order ID, even though the ledger
keeps their full identities separate. Kraken documents a 32-hex-character
short UUID form for `cl_ord_id`:
https://docs.kraken.com/api/blog/cl-ord-id/

## Scope

- Derive `cl_ord_id` deterministically from the first 32 hexadecimal
  characters of the intent identity, retaining 128 bits instead of 56.
- Apply the same derivation in build and validation, and keep it bound to
  the preflight content identity and unsigned payload.
- Cover two distinct full identities with an identical former 14-character
  prefix, and preserve existing tamper and source-binding checks.

## Boundary

The wider identifier reduces collision risk; it cannot guarantee uniqueness
or prove exchange acceptance. It changes the client ID produced for an
existing intent, so a saved preflight from an older build must be rebuilt.
No previously submitted economic order exists in this implementation.

No credential, nonce, signature, HTTP/socket transport, retry, economic call,
or Live capability is added. Kraken-only, Spot BTC/EUR, offline and
fail-closed. `REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
