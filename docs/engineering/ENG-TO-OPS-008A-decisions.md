# ENG-TO-OPS-008A — Controlled Submission Safety Closure

Implementation record for the CTO-authorized 008A mission, based on
`48ed9517454232800624bf6c0ca8a36e2f77e520`. Pending CTO review; no merge or economic
execution authority is implied. ENG-TO-OPS-008 remains NO-GO pending a new readiness review.

## Explicit operator composition

`--execute` is an explicit foreground action, separate from check-only/prepare/watch.
It requires the existing credential capability attestation, exact source/Release/TQ,
external activation pin, real Strategy/Risk approval, exact first-order authorization
and its independently supplied external pin. Missing inputs remain blocked. Watch
continues to use only check-only preparation; there is no watch-to-execute promotion,
replacement pin, automatic authorization renewal or fallback order.

The new `prepare_operator_execution` shares candidate preparation with check-only,
but only the operator entry point accepts execution credentials. `execute_controlled`
checks the provider's credential reference against the activation context and requires
BTCUSDT / TESTNET / BUY / MARKET and the exact current quote cap. The ordinary HTTP
transport still refuses SUBMIT. The first-order HTTP transport additionally requires
a `FinalBoundary`, the process-local one-use permit and the exact durable reservation
in the canonical campaign ledger.

## Durable campaign, not temporary sessions

CTO-designated canonical operational file:

`/Users/anthonylvtt/Library/Application Support/ATP/first-testnet-order/submission.sqlite`

This path is fixed in operator wiring, not selected by `--session-dir`, the checkout,
release version, HOME environment changes or a caller-provided CLI path. Existing
check-only session journals remain non-economic. Tests override the wiring only to
isolated temporary journals.

The canonical directory/file was inspected by filesystem metadata during 008A and
was absent. No legacy operational ledger is claimed; no operational journal was
created, migrated or mutated. Provisioning, restrictive ownership/permissions and
inspection on the intended local durable volume are prerequisites for a later,
separately authorized readiness phase. This mission cannot certify durability of a
nonexistent production file. Execution never provisions or replaces it.

The existing SQLite records, hashes, append-only triggers and schema are preserved.
Any existing record consumes the campaign, regardless of a new authorization/client
ID. `BEGIN IMMEDIATE` serializes the inspect-and-reserve transaction across processes;
the in-transaction check blocks a second reservation with a different authorization.
`PRAGMA synchronous=FULL` and commit precede all economic transport. Missing, corrupt,
unwritable or symlinked operational storage blocks. Multiple historical reservations
fail inspection. No reset, deletion, migration or automatic replacement API is added.

An administrator rolling back/replacing the trusted local database is outside this
local durability model. Multiple hosts/accounts are not silently treated as one
campaign; this implementation targets the explicitly designated Mac campaign path.

## Final evidence and time

A refresh happens before reservation, then again after HTTP connection establishment
and before signing/writing the POST. It reads trusted server time, symbol filters,
account, complete openOrders and exchange price. Filters are re-read every time,
which is stricter than reusing a still-valid filter observation. Request-start times
are used conservatively for filters/account/openOrders, so a slow GET cannot appear
newer than it is. Price uses the exchange effective timestamp.

Fresh portfolio Risk is evaluated using the existing V1 policy. Non-APPROVED blocks.
Changed balances/exposure/open-order identities block even if a new Risk evaluation
would approve. Existing proof, quantity, authorization, receipts and clientOrderId
are never replaced. The full existing gate runs on refreshed price/filter/orders.
Account evidence is conservatively limited to 10 seconds. Final openOrders/account
checks require nonfuture complete evidence, valid through exactly 10 seconds.

The transport permit carries complete-openOrders and its inclusive expiration:
exactly 10 seconds valid, greater than 10 blocked. It retains the existing exclusive
price/filter/authorization transport deadlines. The final callback checks evidence
and trusted clock again immediately before the HTTP request write. A refresh does
not extend the original permit deadlines: if either original or refreshed evidence
fails, there is no POST. A failure after ATTEMPT_STARTED leaves the campaign consumed
and returns UNKNOWN, including when the economic call count is zero.

`UNKNOWN` never retries or releases a reservation. Crashes leave the durable attempt
consumed. A new session or newly approved authorization cannot submit another order.

## Reconciliation

`reconcile_first_order` now verifies the canonical ledger identity declared by the
authorization and the exact reserved authorization/client pair. Reconciled evidence
includes authorization, ledger and reservation identities as well as the existing
symbol/side/type/order/client/fill checks. A copy opened at another path fails.
Exchange cumulative quote, when supplied, must equal the complete fill quote total.

`reconcile_controlled` is a separate explicit read-only API usable after restart or
authorization expiry. It requires the canonical reservation before GET order by
clientOrderId and GET myTrades by established orderId. Only these signed GET routes
are exposed by its source. It does not submit, renew trust, retry, cancel or poll.
A bounded incomplete trade response remains UNKNOWN; an operator must explicitly
request later read-only reconciliation. A reconciled NEW/PARTIALLY_FILLED state is
not a terminal fill, and does not restore economic permission. Accounting is unchanged.

## Cap and forbidden actions

`FIRST_ORDER_QUOTE_CAP = Decimal("6")` remains the **PRE-SUBMISSION PROJECTED**
quote-notional authorization cap for Testnet V1. Quantity-based MARKET BUY does not
guarantee final executed spend <= 6 USDT. This accepted Testnet limitation does not
establish a future Live contract. No quoteOrderQty or changed order semantics.
Old 5 USDT authorization/receipt identities cannot approve this 6 USDT composition.

No Live, withdrawal, cancel, automatic retry or background execution was introduced.
All exercised submissions in tests terminate in synthetic HTTP transports; real
sockets are prohibited by the new test fixture.

`REAL_ECONOMIC_CALLS = 0` throughout implementation and validation.
`LIVE = LIVE_FORBIDDEN`.

## Validation and remaining operational gate

Offline coverage includes all missing gates, post-connection exposure changes,
stale price/orders/account, exact openOrders deadline, old cap/pin, authorization
mismatch, notional over cap, commit failure, restart and two independent processes
contending with different authorizations, UNKNOWN, copied ledger and reconciliation
identity/quote mismatches. Existing crash/expiry tests remain active.

Delivery requires Ruff, strict mypy, full pytest, ATP and TQ on the committed HEAD,
and a draft PR. Tests passing does not provision the production ledger, validate
live account facts, approve merge, or authorize any Testnet submission. After CTO
review, ENG-TO-OPS-008 must return to READINESS REVIEW on the approved exact revision.
