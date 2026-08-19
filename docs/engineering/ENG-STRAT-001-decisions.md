# ENG-STRAT-001 — Strategy baseline foundation

## Status

Implementation record for CTO review.

## Normative sources

This implementation follows the accepted Strategy contracts summarized by the CTO from `SPEC-STRAT-001` and the explicit `ENG-STRAT-001 BASELINE ALGORITHM` decision.

## Scope

The implemented vertical slice is deliberately narrow:

```text
validated causal DATA
→ deterministic single-symbol SMA crossover evaluation
→ typed Strategy signal or explicit blocked evaluation
```

Strategy produces no Risk authorization, order, quantity, position state, Exchange action, AI/ML output, or Live behavior.

## Algorithm and configuration

`SmaCrossoverStrategy` uses only candle `close` values. Its immutable configuration contains `short_window` and `long_window`, with `short_window >= 1` and `long_window > short_window`. No default trading parameters, tuning, optimization, interpolation, or price substitution are present.

The evaluator compares the short and long SMA at the two latest causal instants. It emits `LONG_ENTRY` or `EXIT` only for an effective crossing. Every other valid evaluation emits the explicit `NO_ACTION` signal.

## DATA contract and causality

The baseline explicitly accepts only `VALID`, `FRESH`, `FINAL`, and `NO_GAP_DETECTED` DATA. No numeric freshness threshold is introduced. The existing DATA historical view enforces `available_at <= evaluation_time`, snapshot/universe linkage, historical validation evidence, universe effectiveness, and finality.

Each evaluation covers one symbol. Insufficient history, incompatible DATA or universe evidence, environment mismatch, and invalid `close` input yield `BLOCKED_INPUT` with an explicit reason and no signal or decision identity.

## Provenance and identity

Provenance records the strategy and version, complete immutable configuration and its identity, environment, dataset and snapshot identities, snapshot content identity, schema and transformation versions, canonical DATA lineage identity, universe snapshot and content identities, logical evaluation time, symbol, and every DATA point actually used with its content and temporal evidence. Schema, transformation and lineage evidence are explicit because the DATA snapshot content identity covers points and gaps rather than this reproduction metadata.

The effective SMA series must have unique, strictly increasing `event_time` values. A duplicate or non-monotonic timestamp is rejected as `BLOCKED_INPUT`; Strategy never deduplicates, selects a correction, or counts two versions of one candle as separate periods.

Evaluation, signal, and economic-decision identities are derived deterministically from ATP canonical content. `NO_ACTION` is `COMPLETED` but has no `strategy_decision_id`. `LONG_ENTRY` and `EXIT` are economic proposals only; their decision identities are not order identities.

## Dependencies and architecture impact

No dependency is added. The implementation uses Python stdlib plus the accepted Shared and DATA contracts inside the existing modular monolith. No ADR is required.

## Deliberately deferred

- Multi-timeframe orchestration.
- RSI, ATR, volume and other indicators.
- Strategy optimization, AI/ML, training and feature stores.
- Accounting position state, Risk, sizing, stop-loss and take-profit.
- OMS, orders, Exchange access and Live trading.
- Concrete Strategy persistence and Strategy-to-Risk transport.
