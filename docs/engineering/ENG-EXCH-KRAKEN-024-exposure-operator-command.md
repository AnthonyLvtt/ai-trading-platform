# ENG-EXCH-KRAKEN-024 — exposure observation operator command

Status: **read-only operator command available; no financial PASS or economic authority established**.

Base: `22745c88eb75c553c939ce5ae6c928ed34606e7c`.

The sole explicit command is:

```text
python scripts/qualify_kraken_exposure.py \
  --expected-source-sha <exact-approved-main-sha>
```

The command accepts no API key, API secret, account IIBAN, or authorization
token through argv, environment variables, or files. Sensitive values are
entered interactively and must not be persisted or logged.

Immediately before any network-capable gate is invoked, the operator must type:

`KRAKEN_READ_ONLY_EXPOSURE_OBSERVATION`

The maximum prepared private-read path is:

1. `/0/private/GetApiKeyInfo`
2. `/0/private/BalanceEx`
3. `/0/private/TradeVolume` with `pair=XXBTZEUR`

This is a maximum path, not a guarantee that all three routes run.

Under ENG-KRAKEN-EXPOSURE-002, if BalanceEx contains no EUR row, the command
returns the sanitized INCOMPLETE diagnostic after the first two reads:

```text
status = INCOMPLETE
reason_code = EXPOSURE_GATE_EUR_BALANCE_NOT_OBSERVED
total_private_network_calls = 2
completed_routes = [
  "/0/private/GetApiKeyInfo",
  "/0/private/BalanceEx"
]
runtime_pass_qualified = false
```

TradeVolume is not requested in that case. The absence of an EUR row never
becomes a synthetic zero balance.

## Historical attempt on baseline ce068…

The real bounded read-only attempt performed on
`ce068baca8df94f09181fd060f746193498d2598` occurred before the
ENG-KRAKEN-EXPOSURE-002 behavior was merged. Its historical result remains:

```text
status = FAILED
reason_code = EXPOSURE_GATE_BALANCE_EUR_MISSING
runtime_pass_qualified = false
completed_routes = ["/0/private/GetApiKeyInfo"]
total_private_network_calls = 2
real_economic_calls = 0
side_effect_performed = false
LIVE = LIVE_FORBIDDEN
```

BalanceEx was called but did not yield qualified financial evidence.
TradeVolume was not called.

Do not rerun an identical historical qualification indefinitely to obtain a
different conclusion, and do not deposit funds merely to make this technical
qualification pass. Any new run must follow the then-current reviewed source and
explicit authorization process.

No read-only result authorizes order submission, OMS runtime PASS, Testnet
economic execution, Live, funding, withdrawal, leverage, margin or Futures.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
