# ENG-OMS-KRAKEN-005 — Private source qualification preflight

Status: **offline hardening complete; real read-only observation not qualified**.

Base: `2cfd70f453b6c96d6bfe0488978dbad43ab1f634` (post-merge Quality #190
SUCCESS).

The next qualification targets are exactly the Kraken Spot private sources
`/0/private/BalanceEx` for spendable EUR and `/0/private/TradeVolume` for the
BTC/EUR fee bound. Their payload producers were added by ENG-OMS-KRAKEN-004,
but neither route is signable or allowed by the private transport. Both remain
`observation_qualified=False` and `exposure_source_qualified=False`.
The response semantics are documented by Kraken for
[BalanceEx](https://docs.kraken.com/api-reference/account-data/get-extended-balance)
and [TradeVolume](https://docs.kraken.com/api-reference/account-data/get-trade-volume).

Before any source observation can be qualified, the offline contracts must be
safe against decimal rounding and contradictory fee rows. BalanceEx available
EUR now uses exact decimal arithmetic or blocks when an input exceeds the
supported precision. The TradeVolume fee producer likewise computes
`quantity × limit_price × taker_maxfee / 100` without silent rounding. The
parser rejects a current maker or taker rate outside its declared
`[minfee, maxfee]` range. Synthetic tests cover values beyond Python's default
28-digit decimal precision, contradictory schedules, and continued route
exclusion.

The real read-only qualification remains a **separate operational step**. It
would require an exact clean merged `main` SHA, a dedicated least-privilege
route allowlist and bounded request contract, source/account/credential binding,
freshness and pair checks, an explicit count of the two private reads, and a
sanitized result. Kraken's public BalanceEx example omits credit fields, while
ATP deliberately requires explicit credit facts; that response shape must be
resolved without assuming zero before a spendable-EUR source can qualify.

No real credential, signature, nonce, private network request, economic call,
or Live capability is used in this mission. `REAL_ECONOMIC_CALLS = 0`;
`LIVE = LIVE_FORBIDDEN`. Runtime OMS `PASS` remains unavailable.
