# ENG-EXCH-KRAKEN-024 — exposure observation operator command

Status: **operator command prepared; real execution not authorized by this merge**.

Base: `22745c88eb75c553c939ce5ae6c928ed34606e7c`.

This mission adds the sole explicit command capable of invoking the already
reviewed exposure observation gate:

```text
python scripts/qualify_kraken_exposure.py \
  --expected-source-sha <exact-approved-main-sha>
```

The command accepts no API key, API secret, account IIBAN, or authorization
token through argv, environment variables, or files. The account IIBAN is
entered interactively without echo, normalized only to derive the approved
account identity, then discarded from the local variable. API credentials are
loaded through the existing interactive ephemeral credential loader.

Immediately before any network-capable gate is invoked, the operator must type
the exact confirmation phrase:

`KRAKEN_READ_ONLY_EXPOSURE_OBSERVATION`

The underlying gate still enforces exact clean `main`, exact SHA, API-key and
IIBAN binding, least-privilege permissions, freshness, exact route order and
source stability. The only prepared private-read sequence is:

1. `/0/private/GetApiKeyInfo`
2. `/0/private/BalanceEx`
3. `/0/private/TradeVolume` with `pair=XXBTZEUR`

There is no retry, redirect, fallback, economic route, order submission, OMS
runtime wiring, or Live activation. CI tests replace the gate before it can
perform network I/O.

Merging this command is **not operational authorization to run it**. A real run
still requires separate explicit authorization tied to an exact merged SHA.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
