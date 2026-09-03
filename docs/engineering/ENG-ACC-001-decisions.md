# ENG-ACC-001 — Simulated Accounting Foundation

## Authority

The CTO decision `ENG-ACC-001 V1 SIMULATED ACCOUNTING POLICY` is the normative source for this mission while `SPEC-ACC-001` is absent. Accounting records economic facts; it does not size trades or create orders and fills.

## Input boundary

`AccountingExecution` binds an accepted `SimulatedFill` to a positive finite `Decimal` quantity supplied explicitly by the caller. No default, inference, cash percentage, fixed quantity, or risk sizing exists. The public replay validates runtime types before using external evidence and returns a deterministic `BLOCKED` result for malformed input.

## Policy V1

- policy: `ATP_ACCOUNTING_V1`, version `1.0`;
- currency and quote asset: USDT;
- Spot, long-only, one position;
- external quantity fact;
- single-entry cost basis;
- complete exits only;
- zero fees and no business rounding;
- Decimal-only economic arithmetic;
- DATA `close` as the explicit valuation mark.

## Economic transitions

`BUY_ENTRY` debits `fill_price * quantity`, requires sufficient cash and opens one long position. `SELL_EXIT` requires the same symbol and exact open quantity, credits proceeds, realizes `(exit_price - entry_price) * quantity`, and returns the position to `EMPTY`.

Each applied fill creates exactly one deterministic append-only `AccountingEntry`. Duplicate fills, non-increasing fill times, inconsistent state, invalid fill identity or provenance, and unsupported transitions block the replay without silent repair.

## Valuation

An open position requires a positive finite close mark tied to final, valid, gap-free DATA evidence. The mark must be available no later than the valuation time. Accounting never forward-fills or derives a mark from a fill. V1 enforces:

```text
unrealized_pnl = (mark_price - average_entry_price) * quantity
equity = cash + quantity * mark_price
equity = initial_cash + realized_pnl + unrealized_pnl
```

An empty state needs no mark and has zero unrealized PnL.

## Boundaries and deferred scope

Accounting imports domain contracts from Backtesting, DATA and Shared only. Contract tests prohibit OMS, Exchange, persistence and observability authority. There is no sizing algorithm, allocation, partial execution, leverage, margin, short, futures, network, database, Live reconciliation, deposit, withdrawal, AI/ML, or performance analytics.
