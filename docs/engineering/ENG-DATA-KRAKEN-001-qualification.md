# ENG-DATA-KRAKEN-001 — Kraken Public Market Data qualification

This qualification proves that qualified Kraken public `BTC/EUR` Spot OHLC evidence can
be projected into canonical ATP `FINAL` five-minute DATA records. It grants no private,
economic or Live authority.

## Levels

- `OFFLINE_CONTRACT` uses deterministic repository fixtures and is covered by ordinary
  pytest.
- `PUBLIC_CONNECTIVITY` is explicitly invoked, credential-free and uses only `GET` requests
  to Kraken `Time`, `SystemStatus`, `AssetPairs` and `OHLC` public routes.

Both levels bind the Kraken mapping, candle evidence, input-only ingestion provenance,
lineage, canonical snapshot and exact inspected source. A Kraken failure remains a Kraken
failure; no alternate venue fallback exists.

## Canonical projection

- instrument: `BTC/EUR SPOT`
- interval: five minutes
- finality: `FINAL`
- environments: `LOCAL`, `TEST`, `BACKTEST`, `SIMULATION`
- native Kraken identifiers: confined to adapter mapping evidence

`DRY_RUN`, `TESTNET` and `LIVE` are rejected by the ingestion boundary.

## Invocation

```bash
python scripts/qualify_kraken_market_data.py --level OFFLINE_CONTRACT
python scripts/qualify_kraken_market_data.py --level PUBLIC_CONNECTIVITY
```

The public level requires network access but never credentials. Neither level can submit,
cancel, reconcile or withdraw an order.
