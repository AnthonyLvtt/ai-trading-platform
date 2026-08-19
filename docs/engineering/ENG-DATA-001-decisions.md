# ENG-DATA-001 — Market Data foundation

## Status

Implementation record for CTO review.

## Normative source

The implementation follows `SPEC-DATA-001 v1.0`, canonically Accepted by CTO. The source document's internal `Draft — CTO Review` label is treated as superseded editorial metadata, as directed by the CTO.

## Scope

This change implements only the first DATA vertical slice:

```text
historical input
→ immutable validated snapshot
→ immutable historical universe
→ causally available consumer view
```

It does not acquire live data, contact an Exchange, select a strategy, authorize risk, manage orders, compute accounting state, or choose a storage technology.

## Identities and immutable content

`SourceId`, `DatasetId`, `SnapshotId`, and `UniverseSnapshotId` are distinct types. `ContentIdentity` remains the Shared SHA-256 identity of exact ATP-canonical bytes. Snapshot points and gaps are deterministically ordered before computing the snapshot content identity.

Associating one `SnapshotId` with different content identities is an explicit integrity contradiction. Snapshot and universe constructors also verify supplied identities against their canonical content and fail closed on mismatch.

## Quality, freshness, gaps, and finality

Quality (`VALID`, `DEGRADED`, `INVALID`, `UNKNOWN`) and freshness (`FRESH`, `STALE`, `UNKNOWN`) remain independent fields. `DEGRADED` data requires explicit degradation reasons and an explicit matching consumer allowance. Gap state is explicit and known gaps preserve their interval and reason. Points distinguish `PROVISIONAL` from `FINAL`.

Consumer contracts must explicitly list accepted gap states and point finalities. `KNOWN_GAP`, `GAP_STATUS_UNKNOWN`, and `PROVISIONAL` therefore fail closed unless the consumer names them explicitly.

## Temporal causality

Every point records `event_time`, optional `provider_time`, `ingested_at`, and `available_at` as UTC. Historical reads use `available_at`, never `event_time` alone. A versioned `AvailabilityRule` can derive `available_at` deterministically and remains attached to the resulting temporal evidence.

Historical replay evaluates `validation_as_of_use`; current-state checks evaluate `current_validation_status` through a separate API. A late invalidation remains visible to current checks without rewriting the validation evidence used by a past reproducible run.

## Backfill

`BackfillEvidence` distinguishes current dataset completeness from historical availability. Backfilled content may improve the current dataset without becoming evidence that the data was available during the original historical decision window.

## Lineage and universe

Lineage is a small deterministic sequence of versioned operations and input content identities. It is not an event-sourcing framework.

Universe decisions preserve inclusion and exclusion reasons plus the time their evidence became available. Evidence later than the universe's effective time is rejected, preventing both inclusion and exclusion look-ahead. Universe membership remains DATA eligibility only; it is not Risk, order, or Live authorization.

## Dependencies and architecture impact

No runtime or development dependency is added. The implementation stays inside the existing DATA boundary of the accepted modular monolith and requires no ADR.

## Deliberately deferred

- Provider and Exchange adapters.
- Physical persistence and retention adapters.
- Realtime ingestion, reconnect, and reconciliation orchestration.
- Quantitative freshness, liquidity, or universe thresholds.
- Strategy, Risk, OMS, Accounting, and executable trading behavior.
- DATA health publication and full Observability integration.
- Live activation of any kind.
