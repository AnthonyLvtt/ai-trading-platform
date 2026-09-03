# ENG-BT-001 implementation decisions

## Authority

The CTO decision `ENG-BT-001 V1 BACKTEST / SIMULATED EXECUTION POLICY` is the temporary normative source because `SPEC-BT-001` was absent from `main` at the required base SHA.

## Causal execution

Each replay step binds one reproducible Strategy evaluation and its Risk processing result to the final DATA bar used as evaluation bar `T`. An economic order exists only when the linked Risk decision is `APPROVED`.

The engine selects exactly the first later bar of the same symbol. It never searches beyond an inadmissible candidate. The fill price is exclusively `open(T+1)`. The simulated fill time is the candidate bar's `available_at`, so no fill predates historical availability. Strategy evaluation time and order creation time equal the evaluation bar event time.

The replay tracks when the latest filled state becomes causally effective. If a following Strategy/Risk evaluation predates that fill time, replay blocks with `CAUSAL_STATE_NOT_AVAILABLE`; it neither exposes the future state to economic processing nor creates another order. V1 deliberately fails closed instead of inventing an event scheduler.

## DATA admissibility

V1 accepts only a historically valid, fresh snapshot with `NO_GAP_DETECTED`, final points, consistent immutable provenance, and strictly increasing unique event times per symbol. The evaluation bar must be the final Strategy-used point and must have been available at evaluation time. The next bar must be final, later, symbol-compatible, reproducible, and contain a finite positive open.

Missing end-of-replay data leaves an approved order `UNFILLED_END_OF_REPLAY`. An inadmissible next bar blocks the step without searching for a later bar.

## Structural execution only

`SimulatedOrder` has `BUY_ENTRY` or `SELL_EXIT` side and no quantity. `SimulatedFill` carries the next-bar open and source-bar evidence. The simulated position state is only `EMPTY` or `OPEN_LONG(symbol)`. A mismatch between Risk approval and replay state blocks without repair.

`BacktestResult` contains deterministic structural counts, terminal state, input and policy identities, and step artefacts. It contains no cash, equity, cost basis, PnL, return, drawdown, Sharpe ratio, win rate, fee, slippage, latency, partial fill, allocation, or sizing.

## Non-approved Risk results

- `NO_DECISION` produces `NO_ORDER / STRATEGY_NO_ACTION`.
- `REJECTED` produces `NO_ORDER / RISK_REJECTED`.
- `BLOCKED` produces `NO_ORDER / RISK_BLOCKED`.

The original typed Risk status and reason code remain in each step result. A non-approved result cannot produce an order or fill.

## Boundaries

Backtesting imports accepted DATA, Strategy, Risk, and Shared contracts only. Contract tests prohibit OMS, Exchange, and Accounting imports. There is no network, persistence infrastructure, AI/ML, Testnet, or Live path.

The public `replay()` boundary validates the input container, snapshot, immutable step collection, evaluation bars, and initial simulated state before dereferencing them. Malformed runtime evidence returns a deterministic blocked result with a safe type-based identity when the canonical input identity cannot be established. Unknown objects are never stringified for this fallback identity.
