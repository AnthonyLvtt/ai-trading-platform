# ENG-EXCH-KRAKEN-022 — exposure private observation qualification runner

Status: **read-only runner implemented; complete financial qualification not established**.

The runner requires exact clean `main` at the nominated SHA before loading a
credential. It accepts only a fresh, verified Kraken funds-query capability
bound to the same credential, with trading and withdrawal capabilities absent.

The planned complete sequence is:

1. `BalanceEx`
2. `TradeVolume`

The runner does **not** assume both routes complete.

If BalanceEx yields one explicit complete EUR row, the runner may continue to
TradeVolume and evaluate the full evidence bundle.

If BalanceEx is syntactically valid but does not contain an EUR row,
ENG-KRAKEN-EXPOSURE-002 requires:

```text
status = INCOMPLETE
reason = EXPOSURE_EUR_BALANCE_NOT_OBSERVED
completed_routes = [BalanceEx]
private_network_calls = 1
TradeVolume = not requested
```

This state contains no numeric spendable-EUR value and is never converted to
zero. Source identity is rechecked before the INCOMPLETE result is returned.

Malformed responses, incomplete fields, unsupported assets, stale evidence,
binding failures, API failures, and source drift remain `FAILED` with sanitized
reason codes.

Tests inject in-memory transports and perform zero real private calls. A
simulated `PASSED` result does not qualify a real private source.

A complete financial `PASSED` result remains distinct from connectivity,
permissions, HTTP success, and route observation.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
