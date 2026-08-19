# ENG-RISK-001 — Deterministic Risk Engine foundation

## Status

Implementation record for CTO review.

## Normative source

This implementation follows the explicit CTO decision `ENG-RISK-001 V1 RISK POLICY`.

## Scope

The implemented vertical slice is deliberately narrow:

```text
validated DATA
→ reproducible Strategy proposal
→ deterministic Risk processing result
→ economic Risk decision when applicable
```

Risk creates no order, quantity, sizing, portfolio mutation, Accounting entry, Exchange action, AI/ML output, or Live behavior.

## Processing and decision semantics

Economic proposals produce an immutable `RiskDecision` with `APPROVED`, `REJECTED`, or `BLOCKED`. `REJECTED` means the supplied facts are known and coherent but V1 policy forbids the proposal. `BLOCKED` means reliable authorization is impossible because required evidence is absent, unknown, inconsistent, inactive, or non-reproducible.

Strategy `NO_ACTION` instead produces an explicit `NO_DECISION` processing result with `STRATEGY_NO_ACTION`. It has no `RiskDecision`, `risk_decision_id`, or economic authorization.

## V1 policy

The immutable V1 policy permits only active `LOCAL`, `TEST`, `BACKTEST`, and `SIMULATION` environments. `DRY_RUN`, `TESTNET`, and `LIVE` remain recognized but inactive; unknown environment values have no fallback.

Market evidence is supplied through a Risk-owned context independent of Strategy. V1 requires Spot market and instrument class, long direction, margin disabled, and leverage exactly one. Missing or unknown evidence blocks; a known prohibited value rejects.

`max_positions` is exactly one. A known empty portfolio permits `LONG_ENTRY`, while any existing position rejects another entry. `EXIT` is approved only for an open position on the proposal symbol. It remains an authorization to reduce or exit, never an order or quantity.

Unknown, internally inconsistent, or already policy-violating portfolio state blocks. Risk never assumes that missing state means an empty portfolio.

## Provenance and identity

Risk provenance records Strategy evaluation and proposal references and content identities, Strategy identity and version, Risk policy identity and version, the complete market context and its content identity, the exact raw environment, and the portfolio-state content identity. Status and reason code participate in the decision identity.

The policy, market context, portfolio state, decision, and processing result use deterministic ATP content identities. Identical canonical inputs produce identical results; changing policy or portfolio evidence changes the resulting identity.

## Boundary enforcement

Risk depends on accepted Shared and Strategy contracts only. Import tests prohibit external side effects, while contract tests verify that Strategy and Risk do not import OMS, Exchange, or Accounting. No system clock, network, AI/ML, persistence adapter, or runtime dependency is introduced.

## Deliberately deferred

- Position sizing, capital allocation, notional values, fees, stop-loss, and take-profit.
- Portfolio and Accounting mutation.
- OMS, orders, Exchange adapters, and Binance integration.
- Concrete persistence and asynchronous transport.
- AI/ML, optimization, Web/UI, and Live trading.
