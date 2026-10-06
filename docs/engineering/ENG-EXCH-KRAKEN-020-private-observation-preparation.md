# ENG-EXCH-KRAKEN-020 — BalanceEx / TradeVolume observation preparation

Status: **offline contract prepared; real private observation not authorized or qualified**.

Base: `70c9eea36168c034d652c432abe895bc554febbe` (Quality #192 SUCCESS).

This mission prepares an exact, source-bound read-only qualification contract
for two future Kraken Spot BTC/EUR observations. It makes **zero** private
network calls. The request vocabulary is separate from
`KrakenPrivateReadRoute`, so neither request can be signed or sent by the
existing transport.
The response formats are grounded in Kraken's
[BalanceEx](https://docs.kraken.com/api-reference/account-data/get-extended-balance)
and [TradeVolume](https://docs.kraken.com/api-reference/account-data/get-trade-volume)
documentation.

## Exact proposed requests

| Order | Route | Parameters | Scope |
| --- | --- | --- | --- |
| 1 | `/0/private/BalanceEx` | none beyond a future nonce | default wallet, EUR row required |
| 2 | `/0/private/TradeVolume` | exactly `pair=XXBTZEUR` beyond a future nonce | account-level BTC/EUR Spot fee schedule |

Each request binds one opaque credential-reference identity, the verified
least-privilege capability identity, and the same account
identity. There is no host, method, signature, nonce, credential loader, retry,
or free route parameter in this offline contract. The real route allowlist
remains exactly `GetApiKeyInfo`, `Balance`, and `OpenOrders`.
The supplied account identity is a contract binding, not proof of a real
account-to-credential mapping; that mapping remains part of real observation
qualification.

## Sanitized evidence and PASS/FAIL

Injected responses remain transient values. The qualification result contains
only content identities, timestamps, the EUR available amount or maximum taker
fee percentage, and fixed reason codes. It never contains raw payloads, auth
headers, keys, signatures, or nonces. The source is bound by an exact clean
`main` commit and tree inspected both before and after evaluation.

An `OFFLINE_CONTRACT` result is `PASSED` only when both supplied observations
arrive in the exact request order with matching identities; the existing
credential capability is valid and least-privilege; the BTC/EUR payload parsers
accept both responses; the EUR row is explicit; and the observation times are
ordered, follow the capability observation, and are within 30 seconds of each
other and of that capability observation.
Missing, extra, stale, contradictory, foreign-account, foreign-route, or
malformed evidence returns `FAILED`. The result records two **offline supplied
observations**, `private_network_calls=0`, `real_economic_calls=0`,
`observation_qualified=False`, `runtime_pass_qualified=False`, and
`LIVE_FORBIDDEN`.
The capability evidence is supplied separately; this offline sequence does
not claim a fresh `GetApiKeyInfo` network call.

This `PASSED` status proves only the offline contract. A real observation will
need a separate explicit operational authorization on an exact merged SHA,
locally loaded credentials, a dedicated bounded transport/allowlist, and a new
qualification result counting the actual calls. Kraken's public BalanceEx
example omits credit fields, whereas ATP requires explicit credit facts; that
semantic gap must also be resolved before any spendable-EUR source is marked
qualified. No OMS runtime `PASS` follows from this mission.
