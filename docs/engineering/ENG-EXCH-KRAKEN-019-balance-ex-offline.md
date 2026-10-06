# ENG-EXCH-KRAKEN-019 — Offline BalanceEx interpretation

Status: **offline fixture only; unqualified; no runtime authority**.

Kraken documents the Spot `BalanceEx` available-balance calculation as
`balance + credit - credit_used - hold_trade`. The held amount covers Spot
non-margin orders. Source: [Kraken Get Extended Balance](https://docs.kraken.com/api-reference/account-data/get-extended-balance).

`parse_offline_extended_balance` checks synthetic BTC/EUR response shapes and
calculates that formula only when every operand is explicit. Kraken's published
example omits `credit` and `credit_used`; this parser rejects missing fields
instead of assuming zero. It also rejects unknown/suffixed assets, malformed
numbers, non-finite or negative components, and negative availability.

This parser is deliberately outside the approved private-read route vocabulary.
`/0/private/BalanceEx` is **not** allowlisted or signable and is not added to a
transport, qualification command, operator workflow, exchange port, or OMS/Risk
gate. No account snapshot or spendable-EUR evidence is thereby qualified.
Using the result for a real portfolio assessment would require a separate CTO
mission, an exact qualified source and freshness policy, and resolution of the
missing-credit-field semantics. The proposed 10% BUY-entry policy in
`ENG-OMS-KRAKEN-001` is now an accepted offline design; its runtime PASS remains
unqualified.

Kraken-only, Spot BTC/EUR, offline/fail-closed. `REAL_ECONOMIC_CALLS = 0`;
`LIVE = LIVE_FORBIDDEN`.
