# ENG-KRAKEN-EXPOSURE-002 — EUR balance not observed

Baseline: `ce068baca8df94f09181fd060f746193498d2598`.

`BalanceEx` may return a syntactically valid default-wallet response without
`ZEUR`. That observation is now represented by the distinct
`EUR_BALANCE_NOT_OBSERVED` value kind with `value=None`. It is never interpreted
as a numeric zero and never produces `SPENDABLE_EUR` evidence. An explicit EUR
row, including a valid zero row, still produces numeric `SPENDABLE_EUR` evidence
using the existing exact formula.

The offline contract, private observation runner and operator gate return
`INCOMPLETE` with a source-bound, sanitized diagnostic for this one case. The
runner stops after `BalanceEx`: the complete operator sequence has two private
read calls (`GetApiKeyInfo`, `BalanceEx`) and does not request `TradeVolume`.
`INCOMPLETE` is separate from `PASSED` financial evidence and from `FAILED`
malformed observations. The operator command uses exit code 2 for this
diagnostic; `PASSED` remains 0 and `FAILED` remains 1. No status can enable OMS
runtime `PASS`.

An unsupported asset, malformed envelope, incomplete row, missing credit fact,
stale observation or source change still fails closed. Tests use only synthetic
responses and simulated transports. No new private Kraken call is performed by
this change.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
