# ENG-EXCH-KRAKEN-008 — Kraken Spot order preflight

## Status

Implementation.

## Base

`e56a4b033f61fcbabb98e7a8701c7ea9202f9f5c`

## Scope

Add a deterministic, offline preflight between an approved ATP `OrderIntent`
and any future Kraken economic signing/submission path.

Included:

- exact BTC/EUR → qualified Kraken native mapping binding;
- public metadata identity binding;
- quantity minimum and increment checks;
- limit-price increment checks;
- minimum-notional check;
- fresh bound public price requirement for market-order notional checks;
- deterministic client-order identifier derived from the intent idempotency key;
- canonical unsigned AddOrder payload description;
- tamper-detectable preflight validation.

Excluded:

- credentials;
- nonce generation;
- signature generation;
- HTTP/socket calls;
- AddOrder submission;
- retry;
- cancellation;
- funding/withdrawal;
- margin, leverage, futures or short;
- Live;
- economic qualification or authorization.

## Invariants

`REAL_ECONOMIC_CALLS = 0`.

`LIVE = LIVE_FORBIDDEN`.

The preflight output is data only. It is neither a signed request nor execution authority.
