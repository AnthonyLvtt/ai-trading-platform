# ENG-FOUND-001 — Engineering foundation decisions

## Status

Implementation record for CTO review.

## Decisions

### Python package layout

Use a `src/atp` modular-monolith layout. Domain packages exist as canonical boundaries but contain no invented business logic.

### Dependency management

Use `uv` with `pyproject.toml` and `uv.lock` for reproducible dependency resolution. Runtime foundation code uses only the Python standard library. Development tooling is isolated in the `dev` dependency group.

### Quality tooling

- Ruff: formatting and linting.
- mypy: static type checking in strict mode.
- pytest: executable tests.

### Configuration

Configuration is environment-driven and explicit. `ATP_ENV` has no fallback. Foundation-enabled environments are LOCAL, TEST, BACKTEST, and SIMULATION. DRY_RUN, TESTNET, and LIVE are recognized identifiers but cannot be activated by ENG-FOUND-001. `TEST` is the local automated-test environment and is explicitly distinct from Exchange `TESTNET`; no equivalence is inferred.

### Secrets

Secrets are not part of versioned configuration. `.env` is ignored and `.env.example` contains only non-secret values. Standard tests reject known Live credential variables.

### Observability

Structured JSON logging uses Python's standard logging package. Domain events are distinct from logs. The minimal event envelope preserves `event_id`, logical version, `occurred_at`, `observed_at`, producer, environment, correlation/causation when present, and payload. Secret redaction is applied before handler output and again to the formatted payload, including exception text. Observability remains diagnostic and never becomes domain authority.

### Persistence

Only a minimal repository Protocol is introduced. No database, distributed log, event-sourcing framework, or transaction implementation is selected in this mission.

### Deferred intentionally

- DATA implementation and storage technology selection beyond adapter boundaries.
- Strategy/Risk/OMS/Accounting business logic.
- Backtest engine.
- Binance adapter behavior.
- Web UI.
- full observability infrastructure.
- CI/CD automation and deployment.
- Live trading.

### Normative sources reviewed

- `DOC-MAP-001` Accepted v1.0
- `ADR-001` Accepted v1.0
- `ADR-002` Accepted v1.0
- `SPEC-SEC-001` Accepted v1.0
- `SPEC-OBS-001` Accepted v1.0
- `SPEC-TEST-001` Accepted v1.0
- `SPEC-REL-001` Accepted v1.0

No normative contradiction requiring CTO arbitration was identified in this foundation pass. The Exchange Live protection item remains outside ENG-FOUND-001 and no Live capability is enabled.
