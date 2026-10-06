# ENG-EXCH-KRAKEN-021 — bounded exposure observation transport

Status: **implementation prepared; real private observation still not authorized**.

Base: `c07af35d27b28113526d62be63c4bb8ddfbef01c`.

This mission adds a dedicated, exact transport boundary for the two already
prepared exposure-observation requests:

- POST `/0/private/BalanceEx` with nonce only;
- POST `/0/private/TradeVolume` with nonce plus exactly `pair=XXBTZEUR`.

The exposure allowlist is separate from the existing
`KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST`. No economic route is present. Host,
method, TLS, timeout, response size, headers and parameters are fixed. Request
identity must bind the same local credential reference and nonce provider.

The implementation includes signing and HTTPS transport code so the future
qualification run can be source-bound and exact. Ordinary tests monkeypatch the
connection; CI makes zero private network calls.

This merge does **not** authorize executing the transport. A real observation
still requires a separate explicit operational authorization on an exact merged
SHA and locally entered read-only credentials.

`REAL_ECONOMIC_CALLS = 0`.
`LIVE = LIVE_FORBIDDEN`.
