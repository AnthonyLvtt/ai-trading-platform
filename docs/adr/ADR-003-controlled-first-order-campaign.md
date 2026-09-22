# ADR-003 — Controlled first-order campaign boundary

Status: CTO decision supplied for ENG-TO-OPS-008A; implementation pending PR review.
Base: `48ed9517454232800624bf6c0ca8a36e2f77e520`.

The CTO requires the first Testnet attempt to be campaign-wide, durable and unique,
with account, open-orders, price and clock revalidation immediately before POST.
A session-local authorization journal is insufficient for this boundary.

The implementation uses the explicitly CTO-designated persistent Mac ledger path,
retains the existing SQLite schema and makes every prior attempt consume the entire
campaign. Concurrent reservations are serialized in the same durable transaction.
No execution entry point provisions, selects an alternate journal, or resets history.

The HTTP boundary additionally refreshes evidence after connection establishment,
rechecks Risk and the existing gates without changing approved order facts, and
checks deadlines at request write. Any failure blocks; a reservation already made
remains consumed. Reconciliation binds the reserved authorization/client and ledger.

The 6 USDT cap is projected before submission, not a final MARKET execution-spend
guarantee. Its acceptance is limited to Testnet V1. Live, withdrawal, cancellation,
automatic retries and automated replacement authorizations remain prohibited.

Implementation details, validation and operational prerequisites are recorded in
[ENG-TO-OPS-008A](../engineering/ENG-TO-OPS-008A-decisions.md). No economic runtime is
authorized by this ADR or by a passing PR. CTO review and a new readiness review
precede any separately authorized first submission.
