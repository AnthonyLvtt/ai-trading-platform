# ENG-EXCH-KRAKEN-007 — Kraken Spot execution foundation

## Status

Implementation.

## Base

`5b457c6fbb536321138993979b638fb8a4d74931`

## Scope

Build the offline, fail-closed foundation required before any Kraken Spot economic
qualification exists.

Included:

- canonical BTC/EUR Spot order intent;
- BUY for LONG_ENTRY and SELL for EXIT only;
- exact Strategy/Risk identity binding;
- deterministic idempotency identity;
- durable append-only SQLite transition ledger;
- explicit UNKNOWN ambiguity state and reconciliation transition;
- structural Kraken AddOrder route descriptor;
- disabled economic transport with no network implementation.

Excluded:

- real credentials;
- request signing for AddOrder;
- HTTP/socket calls;
- retries;
- automatic reconciliation;
- cancel/replace;
- funding or withdrawals;
- margin, leverage, futures or short;
- Live;
- any economic qualification or authorization.

## Invariants

`REAL_ECONOMIC_CALLS = 0`.

`LIVE = LIVE_FORBIDDEN`.

The existence of `/0/private/AddOrder` as a route descriptor is not authority to call it.
No network implementation for this route exists in this mission.
