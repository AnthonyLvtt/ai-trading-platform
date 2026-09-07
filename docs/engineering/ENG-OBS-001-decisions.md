# ENG-OBS-001 — Observability Foundation

## Authority

The CTO decision `ENG-OBS-001 V1 OBSERVABILITY / AUDIT POLICY` is the normative source for this mission while `SPEC-OBS-001`, `SPEC-SEC-001` and `SPEC-TEST-001` are absent. Observability records evidence and diagnostics; it has no authority to create or mutate DATA, Strategy, Risk, Backtesting or Accounting facts.

## EVENT != LOG

`ObservabilityEvent` is a structured, deterministic and auditable domain reference. `DiagnosticLogRecord` is a human-facing operational representation. A log may reference an event but cannot replace an audit event or authorize a business action.

Events never contain exceptions, tracebacks, complete domain objects or arbitrary Python values. Logs can redact diagnostic content, while a sensitive event payload is blocked rather than made acceptable through redaction.

## Event contract and taxonomy

The V1 taxonomy is closed to the thirteen event types defined by the CTO policy across DATA, Strategy, Risk, Backtesting and Accounting. Each event uses schema `1.0`, an explicit UTC logical timestamp, a canonical module/category/severity, an `INTERNAL` classification, a correlation identifier and an optional causation identifier.

Domain artefacts are represented only through `subject_type`, `subject_id` and `subject_content_identity`. Payloads contain small deterministic scalar facts, status/reason codes and stable identities. Decimal values, enums, UTC timestamps and content identities are normalized before canonicalization; mappings and sequences are recursively frozen.

The event content identity covers the complete canonical envelope except `event_id` and `content_identity`. The identifier is derived as:

```text
event_id = "obs-event:" + content_identity
```

No random UUID or system clock participates in event identity.

## Correlation, causation and audit chain

The caller supplies a deterministic correlation identifier for the complete logical run. Each event after the first references its direct causal event and the prior event content identity.

`AuditJournal` stores an immutable tuple and returns a new journal on append. Its integrity checks cover event identity, duplicate identifiers, prior-event links and non-decreasing UTC logical time. Equal timestamps are valid and tuple order is authoritative. Journal identity covers schema version plus the ordered event identities, so changing order changes identity.

## Security and runtime boundary

Payload keys are inspected recursively and case-insensitively. Passwords, credentials, authorization values, tokens, private keys, seed phrases and connection strings block event creation with `SENSITIVE_DATA_DETECTED`. They are never retained in a blocked-result identity.

Public construction, adapter and journal validation boundaries check runtime types before canonicalization. Unsupported Python objects produce deterministic blocked results whose fallback identity uses only the object's module and qualified type name; neither `str()` nor `repr()` is invoked on external evidence.

The JSON diagnostic logger also redacts sensitive keys and configured secret values. Exception output is restricted to the stable exception type; exception messages and tracebacks are not emitted automatically.

## Domain adapters

Read-only adapters map DATA snapshots, Strategy evaluations, Risk processing results, simulated orders/fills, Backtest results, Accounting entries/replays and valuations to the closed event taxonomy. They revalidate domain artefact identity before producing an event and accept explicit logical time or environment where the source artefact does not carry it.

The vertical-slice contract test executes the existing DATA → Strategy → Risk → Backtesting → Accounting path, maps the resulting artefacts, and validates a common correlation identifier and complete causation/audit chain.

## Deliberate deferrals

V1 contains no database, remote telemetry shipping, Kafka, OpenTelemetry collector, Prometheus server, Elasticsearch, dashboards, alerting, paging, Slack/email integration, Exchange monitoring, Live health check or AI log analysis. The implementation is local and in-memory and performs no network access.
