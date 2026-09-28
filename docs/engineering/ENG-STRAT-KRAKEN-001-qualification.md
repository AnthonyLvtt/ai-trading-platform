# ENG-STRAT-KRAKEN-001 qualification

Status: implementation candidate

## Scope

This qualification proves the pure analytical path:

```text
canonical Kraken BTC/EUR Spot CLOSED 5m DatasetSnapshot
→ deterministic one-symbol UniverseSnapshot
→ existing SMA 20/50 Strategy
→ existing StrategyEvaluation
```

It creates no Risk approval, order intent, position or Accounting mutation, Exchange
submission, credential capability or runtime authorization.

## Level

`OFFLINE_CONTRACT` is the only qualification level. Public Kraken acquisition through
canonical DATA remains owned by `ENG-DATA-KRAKEN-001`; this qualification performs no
network request.

The deterministic fixture contains 52 raw Kraken rows. The existing Kraken parser removes
the final uncommitted row and yields exactly 51 CLOSED candles, the minimum required for
the accepted SMA 20/50 evaluation.

Run on an exact clean source tree:

```bash
python scripts/qualify_kraken_strategy.py
```

## Universe and time

The universe contains only canonical `BTC/EUR`, binds the exact DATA `SnapshotId`, and uses
`ENG-STRAT-KRAKEN-001/1.0`. Its ID is derived from an acyclic manifest containing the
dataset, environment, snapshot identities, symbol, rules version and effective time.

`created_at`, `effective_at`, decision `evidence_available_at`, and Strategy
`evaluation_time` all equal `DatasetSnapshot.created_at`. This is the ingestion observation
time already used to make every qualified candle causally available.

## Safety

- allowed environments: `LOCAL`, `TEST`, `BACKTEST`, `SIMULATION`;
- `DRY_RUN`, `TESTNET`, and `LIVE` are rejected;
- no native Kraken alias leaves Exchange evidence;
- no Strategy model, serialization or identity rule is changed;
- `candle_interval` is not added to non-TESTNET canonical Strategy provenance;
- no Risk, OMS, private/economic Exchange or Accounting path is invoked;
- `REAL_ECONOMIC_CALLS = 0`;
- `LIVE = LIVE_FORBIDDEN`.
