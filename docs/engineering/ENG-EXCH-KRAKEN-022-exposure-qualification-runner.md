# ENG-EXCH-KRAKEN-022 — exposure private observation qualification runner

Status: **offline implementation prepared; operational execution not authorized**.

The runner requires exact clean `main` at the nominated SHA before loading a
credential. It accepts only a fresh, verified Kraken funds-query capability
bound to the same credential, with trading and withdrawal capabilities absent.
It sends exactly one `BalanceEx` and one `TradeVolume` request through the
dedicated two-route transport, in order. Requests are bound to the same account,
credential reference and capability. Response identity, time sequence, freshness
and strict payloads are checked before a sanitized result can be `PASSED`.
The source is inspected again after both observations. Any mismatch fails
closed. The result counts private network calls, records the source tree and
request identities, and contains only sanitized proofs, never raw responses or
credential material.

Tests inject an in-memory transport; they perform zero private network calls.
A simulated `PASSED` result does not qualify a real private source. There is no
runtime OMS wiring, no live activation, and no operator command in this change.
Calling the runner with the HTTP transport and local credentials requires a
separate explicit operational authorization on a reviewed merged SHA.

`REAL_ECONOMIC_CALLS = 0`; `LIVE = LIVE_FORBIDDEN`.
