# ENG-TO-OPS-008D — watcher preparation stop point

Base: `a53c389c909428dd95b7e1580c293a504861b7e1`.

## Explicit operator contract

`--watch-prepare-first-order` is a distinct mutually exclusive CLI mode. It runs the
bounded watcher, retains the existing external activation-grant review and pin flow,
and stops after a genuine LONG_ENTRY has passed fresh private reads, Strategy, Risk,
quantity selection and the ordinary candidate gates.

The mode saves the `FirstTestnetOrderAuthorization` and its non-secret preparation
evidence, then returns `READY_FOR_CTO_REVIEW` with reason
`FIRST_ORDER_AUTHORIZATION_REVIEW_REQUIRED`. It never calls the first-order pin
source, constructs a first-order trust receipt, invokes controlled execution or
creates a submission permit.

The existing `--check-only --watch`, `--prepare` and `--execute` contracts retain
their prior semantics. The ordinary check-only and explicitly authorized execution
paths still require the separate first-order pin.

## Campaign and lifecycle

The preparation watcher opens the canonical operational ledger and requires the
campaign to be empty. It uses the ledger identity in the candidate but performs no
append. The candidate therefore reports `ledger_state=NOT_ATTEMPTED`; no
`ATTEMPT_STARTED` can be produced by this mode.

NO_ACTION and EXIT continue only until the already pinned activation grant expires.
There is no renewal. Expiry, stale evidence, invalid source binding or any unknown
input blocks. Credential reference, capability and runtime authorization context
remain the same session-bound objects from activation through candidate creation.

## Safety

The mode remains TESTNET / BTCUSDT / BUY MARKET with the existing six-USDT projected
quote cap and mandatory Risk. It introduces no transport or economic dependency.
Tests forbid network/economic transports and prove that the first-order pin reader,
trust receipt and `execute_controlled` are not reached.

`REAL_ECONOMIC_CALLS = 0`

`LIVE = LIVE_FORBIDDEN`
