# AI Trading Platform

ATP is a modular algorithmic-trading platform. V1 is Kraken-only, Spot, LONG only, with a single position.

This repository contains the modular-monolith engineering foundation and the V1 module boundaries: Market Data, Strategy, Risk, OMS, Exchange Adapter, Accounting, Backtesting/Simulation, Observability, Web supervision, Test/Qualification, Release/Deployment and Operations.

Kraken is the sole venue. The canonical initial instrument is `BTC/EUR` Spot. Public connectivity and bounded private read-only capabilities are qualified independently. No economic Kraken execution is authorized by installation, CI, merge, credentials, or read-only qualification. Live trading is forbidden, and credentials are never stored in the repository.

## Requirements

- Python 3.12
- `uv`

## Local setup

```bash
uv sync --dev
```

## Quality gate

```bash
make validate
```

Individual commands:

```bash
make format
make lint
make typecheck
make test
```

## Foundation diagnostic

```bash
make diagnostic
```

The diagnostic loads an explicit environment, emits a structured JSON log, and does **not** contact an Exchange.

## Repository layout

- `src/atp/shared` — typed identifiers, canonical content identity, UTC clocks and common technical primitives
- `src/atp/data` — immutable Market Data snapshots, causal historical views, lineage, and universe primitives
- `src/atp/strategy` — Strategy boundary
- `src/atp/risk` — Risk boundary
- `src/atp/oms` — OMS boundary
- `src/atp/accounting` — Accounting boundary
- `src/atp/backtesting` — Backtesting/Simulation boundary
- `src/atp/observability` — structured logs/events
- `src/atp/test_qualification` — TEST/qualification boundary
- `src/atp/ops` — Operations boundary
- `src/atp/web` — Web supervision boundary
- `src/atp/exchange` — canonical Exchange boundary and Kraken adapter
- `src/atp/kraken_qualification` — Kraken qualification
- `src/atp/kraken_private_qualification` — bounded private read-only Kraken qualification
- `src/atp/release_deployment` — Release/Deployment boundary
- `src/atp/persistence` — persistence ports/adapters boundary
- `scripts` — operator and qualification commands
- `tests/unit` — unit tests
- `tests/contract` — boundary/contract tests
- `docs/adr` — Accepted architecture/governance decisions available in this repository
- `docs/engineering` — engineering implementation records
- `docs` — location for normative ATP documentation

## Normative authority

Accepted ATP normative documents under `docs` are the source of truth. Code must not silently redefine domain state machines or authority boundaries.

See `CONTRIBUTING.md` and `docs/engineering/ENG-FOUND-001-decisions.md`.

The Accepted `SPEC-*` documents cited by the ADRs and engineering records are not yet present under `docs`. Their reintegration is a separate documentation task; engineering records here are not a substitute for them.
