# ENG-TO-001 — First Testnet Order Authorization

Authority: CTO mission ENG-TO-001 and the complementary CTO decision
IDENTITY / FRESHNESS / ACCOUNTING BOUNDARY. Base:
`4286a5db74985c76bddb6e4d5e1c68fdf1d95787`.

## Scope and trust

Qualification is not authorization. `ATP_FIRST_TESTNET_ORDER_V1 / 1.0` permits
at most one explicitly requested attempt, BUY / Spot / MARKET / TESTNET only.
There is no sizing, default amount, cancellation, automatic exit, retry, or
background execution. LIVE and withdrawal remain forbidden.

The delivered normal composition has no production activation authority,
first-order authority, credential capability authority, real grant or credential.
The CLI therefore remains BLOCKED in both explicit modes. No order is sent by
validation, qualification, CI, import, merge, or startup.

The first-order authority is an injected abstract composition dependency. It
must pin the **final** authorization identity. Its evidence is converted to a
process-local receipt; constructing or copying a receipt does not convey trust.
The existing activation context continues to attest the exact source/tree,
Release, TQ, credential source and capabilities. Risk, OPS and Release consume
that context through their existing public gates; their policies are unchanged.

## Authorization identities

`FirstTestnetOrderAuthorization` contains schema/policy versions, TESTNET,
activation/context/source/release/TQ bindings, upstream proof identity, symbol,
BUY, MARKET, exact Decimal quantity, projected quote cap, validity window and
max_submissions=1. It additionally binds the canonical local submission ledger
identity so selecting a second journal cannot reset the authorization.

1. `authorization_facts_identity`: all normative initialization fields, excluding
   client_order_id and the two identities.
2. `client_order_id`: the existing public Exchange builder applied to the facts
   identity (`atp-` plus its 32-character digest prefix).
3. `content_identity`: complete canonical authorization including facts identity
   and client ID, excluding only its own identity.

All three are recomputed before trust or use. The quantity is bound to the
existing external `UpstreamOrderProof`, never inferred from portfolio or filters.
The first-order request uses the approved authorization's client ID; the ordinary
Exchange request convention and legacy adapter remain unchanged.

## Final gate and time

`evaluate_first_order` validates trusted receipts and every existing submission
prerequisite via the public Exchange inspection facade. The TA public gate still
returns TESTNET_RUNTIME_BLOCKED. The new explicit runner additionally requires
all first-order gates, the pinned ledger, a matching credential-source transport
and the fixed endpoint `https://testnet.binance.vision`.

A `GateClock` is injected. `ProcessGateClock` centralizes the process-clock read
and takes an explicitly injected server-time source. There is no default network
source. The Exchange module provides the public read-only serverTime parser.
The ephemeral `GateTimeSample.gate_evaluation_time` does not enter proof identities.
The sampled server/local time evidence has its own deterministic identity.

Maximum absolute clock skew is five seconds. Filter evidence must be at most
15 minutes old; price evidence at most 10 seconds old. Existing causal contracts
continue to reject future filter/price observations, including those within the
five-second skew bound. No prior check-only result is reusable permission.

The runner acquires SQLite's write reservation, repeats the full gate, and
samples time again **after** expensive prerequisite verification and request
hashing. Only then does it persist ATTEMPT_STARTED. Stale evidence or an expired
authorization aborts the transaction without consuming the authorization.

For applicable Binance notional filters, the TQ price source/window rules apply.
Otherwise the ATP cap requires EXCHANGE_LAST_PRICE. DATA_MARK cannot authorize
submission. Decimal multiplication uses sufficient local precision to avoid
caller-context rounding; no quantity adjustment occurs.

The cap is a **PRE-SUBMISSION PROJECTED NOTIONAL CAP**, not a guaranteed maximum
spend. MARKET BUY with base quantity can execute at a different book price. ATP
refuses a freshest admissible projection exceeding the CTO cap; this does not
impose an Exchange-side execution bound.

## Durable attempt ledger

`TestnetSubmissionLedger` is explicitly provisioned once in a trusted local
filesystem, with restrictive file permissions. Opening a missing/corrupt ledger
fails closed; the runner never creates or resets it. SQLite transactions,
`BEGIN IMMEDIATE`, `synchronous=FULL`, unique authorization/client reservation
indexes, append-only triggers and a checked hash chain provide local durable
single-use behavior, including concurrent callers and process restart.

The sequence is NOT_ATTEMPTED -> ATTEMPT_STARTED -> ACKNOWLEDGED / UNKNOWN /
RECONCILED. ATTEMPT_STARTED is committed before any transport call. A crash at
any later instruction leaves the authorization consumed. Ledger failure before
commit means zero submissions; failure recording the outcome preserves the
reservation and returns UNKNOWN. A new ledger path cannot reuse the authorization.

This relies on trusted local storage: an administrator replacing the database
with an old backup is outside this local durability model. No remote/NFS storage,
rollback service, reset API or automatic ledger recovery is introduced.

A process-local single-use transport permit is issued only after durable
reservation. It binds the exact request, credential source, readiness and gate
time. The HTTP adapter consumes it before accessing credentials or signing.
The ordinary HTTP `perform(SUBMIT)` defense remains blocked.

## Outcome and reconciliation

ACK records only typed IDs/status/time/response identity, never raw exchange JSON.
ACK does not establish a fill or position. Ambiguous timeout, malformed response
or uncertain transport outcome becomes UNKNOWN; the authorization stays consumed.
A certain authentication/exchange rejection is retained as REJECTED, never
misrepresented as unknown, and still cannot produce another attempt.

The next action after ACK/UNKNOWN is explicit read-only reconciliation. Existing
Exchange client/order-ID lookup descriptors and parser are reused with injected
lookup responses. There is no order retry, cancellation, polling loop or automatic
network reconciliation. A not-found response without sufficiently authoritative
proof remains UNKNOWN; it never restores submission permission.

The reconciler checks the originally acknowledged exchange ID when present,
exact symbol/side/type/quantity, causal times and complete supplied fills before
producing `ReconciledExchangeExecutionEvidence`. Partial fills remain explicit;
unknown statuses or incomplete fills remain unresolved. All amounts are Decimal.

The evidence contains environment, symbol/side, order/client IDs, status, fills,
cumulative base/quote quantities, source identity and reconciliation time. It
stops at this read-only record: no Accounting import, mutation, cash/PnL calculation
or ExchangeFill-to-SimulatedFill conversion. ENG-ACC-EXCH-001 must define real
Accounting ingestion separately.

## Manual command and delivery boundaries

`python scripts/submit_first_testnet_order.py --check-only` is the preparation
mode; `--execute` is a separate explicit action. Omitting a mode is rejected.
Both remain blocked in this delivered CLI because production authorities are
absent. The injected Python runner demonstrates READY_TO_SUBMIT with synthetic
complete evidence and zero transport calls; tests alone inject a fake transport
to exercise one attempt and reconciliation.

No CLI grant upload, environment enable flag, credential dump, Web control,
production allowlist or real grant is supplied. Future post-merge composition
requires the exact CTO-approved grant and first-order authorization, trusted
credential/source/time boundaries, a preserved ledger and a separate manual
execution decision. No network permission probe is inferred from authentication.

## Validation

Tests cover both identity levels/client ID, pre-pinning mutation, untrusted
receipts, source/scope/quantity/time gates, price provenance, exact cap arithmetic,
durable reservation failures/concurrency/crash/restart, secret-safe errors,
check-only, one fake ACK, no second attempt, UNKNOWN and read-only reconciliation.
Socket creation is forbidden in these tests. Existing ATP and TQ catalogues,
closed Observability taxonomy, Accounting and Risk/OPS/Release policies are kept.

## CTO review correction — transport deadlines

The process-local permit binds absolute authorization/context expiry, price
freshness expiry and filter freshness expiry. The transport takes an injected
trusted GateClock; absence, invalid time evidence, clock regression or reaching
any deadline fails closed. Transport deadlines are exclusive (`now < deadline`).

Time is checked when consuming the one-use permit, before credential loading,
after credential loading, after connection establishment before signing, and
immediately before the HTTP request write. The HTTP timestamp uses the fresh
sample. A suspension during transport preparation therefore cannot reuse the
reservation-time sample as permission. No production clock source is installed
implicitly.

Expiration after ATTEMPT_STARTED never deletes or releases the reservation.
It returns UNKNOWN with zero economic transport calls and requires intervention;
rewinding a test clock or restarting cannot allow another attempt. Regression
tests cover authorization/price/filter deadlines (including equality), delayed
credential loading and delayed connection establishment, with fake HTTP only.
