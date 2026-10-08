# ENG-EXCH-KRAKEN-020 — BalanceEx / TradeVolume observation preparation

Status: **offline contract prepared; real private exposure source remains unqualified**.

Base: `70c9eea36168c034d652c432abe895bc554febbe` (Quality #192 SUCCESS).

This mission prepares an exact, source-bound read-only qualification contract
for two Kraken Spot BTC/EUR observations. It made **zero** private network calls
at the time of this mission. The response formats are grounded in Kraken's
BalanceEx and TradeVolume contracts.

## Exact prepared requests

| Order | Method and host | Route | Parameters | Scope |
| --- | --- | --- | --- | --- |
| 1 | POST `api.kraken.com` | `/0/private/BalanceEx` | nonce only | default wallet |
| 2 | POST `api.kraken.com` | `/0/private/TradeVolume` | nonce + exactly `pair=XXBTZEUR` | account-level BTC/EUR Spot fee schedule |

Each request binds one opaque credential-reference identity, the verified
least-privilege capability identity, and the same account identity.

## Evidence semantics

A numeric `SPENDABLE_EUR` proof exists only when one explicit, complete EUR row
is observed and every required amount is explicit.

Absence of an EUR row is **not** proof of a zero balance and must never synthesize
`SPENDABLE_EUR = 0`.

As implemented by ENG-KRAKEN-EXPOSURE-002, a syntactically valid BalanceEx
response with no EUR row produces:

```text
status = INCOMPLETE
reason = EUR_BALANCE_NOT_OBSERVED
value = None
runtime_pass_qualified = false
```

That path stops before TradeVolume. It is distinct from both a fully qualified
financial `PASSED` result and malformed/contradictory `FAILED` evidence.

An explicit valid EUR row containing numeric zero remains a genuine numeric
`SPENDABLE_EUR` proof; it is not equivalent to an absent EUR row.

Missing required fields in a present row, unsupported assets, malformed
envelopes, stale evidence, foreign bindings, or source drift remain fail-closed.

The original offline `PASSED` status proves only the supplied contract and does
not qualify a real private source or authorize OMS runtime `PASS`.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
