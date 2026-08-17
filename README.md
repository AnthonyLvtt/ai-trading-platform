# ATP — AI Trading Platform

ATP is a modular algorithmic-trading platform. The current repository phase is **engineering foundation only**: no real trading, no Binance integration, and no Live credentials are required.

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

- `src/atp/shared` — technical primitives only
- `src/atp/data` — Market Data boundary
- `src/atp/strategy` — Strategy boundary
- `src/atp/risk` — Risk boundary
- `src/atp/oms` — OMS boundary
- `src/atp/accounting` — Accounting boundary
- `src/atp/backtesting` — Backtesting/Simulation boundary
- `src/atp/observability` — structured logs/events
- `src/atp/qualification` — TEST/qualification boundary
- `src/atp/ops` — Operations boundary
- `src/atp/web` — Web supervision boundary
- `src/atp/exchange` — Exchange Adapter boundary
- `src/atp/release` — Release/Deployment boundary
- `src/atp/persistence` — persistence ports/adapters boundary
- `tests/unit` — unit tests
- `tests/contract` — boundary/contract tests
- `docs/adr` — Accepted architecture/governance decisions available in this repository
- `docs/engineering` — engineering implementation records
- `docs` — location for normative ATP documentation

## Normative authority

Accepted ATP normative documents are the source of truth. Their canonical repository location is under `docs`; this foundation change includes the supplied ADR-001/ADR-002 and does not recreate missing SPEC files from secondary references. Code must not silently redefine domain state machines or authority boundaries.

See `CONTRIBUTING.md` and `docs/engineering/ENG-FOUND-001-decisions.md`.
