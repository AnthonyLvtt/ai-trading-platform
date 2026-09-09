# ENG-WEB-001 — Minimal Operational Web Visibility

## Normative source and authority

The CTO V1 read-only Web decision replaces the absent SPEC-WEB-001 for this mission.
Base: `2eee968f1b5cd9d8a1d3c86b0b9bcf8ee011dcc7`.

Web is a presentation adapter. It does not run Strategy, Risk, Backtesting,
Accounting, Qualification, OPS startup/shutdown, or any economic operation.
Qualification PASSED, health HEALTHY, and HTTP access grant no trading authority.
LIVE and TESTNET cannot be enabled here.

## HTTP surface

FastAPI is the explicitly selected adapter, pinned in pyproject.toml and uv.lock.
HTTPX is a development dependency for in-process TestClient requests.
No server/process manager or frontend package is added.

`create_app(state)` returns an importable application. Six paths accept GET and HEAD:

- `/health`
- `/readiness`
- `/qualification`
- `/observability`
- `/backtest/latest`
- `/accounting/latest`

There are no business mutation methods, callbacks, startup jobs or run endpoints.
Docs, ReDoc, OpenAPI and slash redirects are disabled. No CORS, cookies or sessions.
HEAD has the same status/headers as GET with an empty body.

Intended bind is **loopback only: 127.0.0.1 / ::1**. The application does not start
or configure a listener. No authentication is added under the CTO local-only policy;
this is not permission to expose the app remotely. Remote exposure is outside V1.

FastAPI documentation consulted:
https://fastapi.tiangolo.com/tutorial/testing/
https://fastapi.tiangolo.com/tutorial/metadata/

## Input and integrity boundary

WebState is a frozen snapshot of explicitly injected references. No database,
filesystem scan, environment dump, external network or polling is performed.

`reference(artifact, causal_input=...)` validates the domain record and captures its
content identity plus a recursive fingerprint. This captures fields omitted from a
domain content hash as well. It returns a typed safe error on bad input. The reference
must be made at trusted composition/production, before handing the state to Web.
Pinning is an integrity anchor, not authentication of the producer.

Every request revalidates the complete injected state before projection. Unknown
runtime objects are refused before any str/repr call; record fields are checked
against the accepted types before domain model validators are used. Cyclic/deep
structures fail closed. The allowed record-type set is captured from the loaded ATP
contracts, not inferred from an external object's claimed module name.

Existing model post-init guards validate stored identities and derived IDs. Accounting
replay integrity uses its existing ledger validator; valuations use canonical identity
reconstruction without invoking valuation. Qualification PASSED validation reuses the
OPS integrity verifier; FAILED/BLOCKED remain displayable without granting authority.
Readiness verifies check aggregation and preserves the existing status/reason.
AuditJournal uses the accepted journal validator. No audit payload is returned.

For health, the caller injects the **already computed** HealthStatus with its
OperationalHealthEvidence in HealthResult. The Web checks the evidence identity and
pins the pair; it does not run OPS health aggregation or derive readiness.

Backtest references also carry the associated BacktestInput. The result alone does
not retain evaluation times for NO_ORDER steps. This causal evidence is checked and
linked by input_identity, and is never included in the HTTP response.

Links between readiness/qualification, valuation/accounting state and
accounting ledger/backtest fills are checked when those sources are co-injected.
A mismatched link blocks the snapshot. Missing optional related sources are not
fabricated. A reference is not a cryptographic signature or a proof of origin.

## Latest and deterministic views

All collection members are validated before selection; invalid entries never cause
fallback to older data. Duplicate candidate identities are inconsistent.

Backtests use the existing Observability causal-time derivation: processed evaluation
times, order.created_at and fill.fill_time. Snapshot.created_at is excluded.
Tie-break is maximal lexicographic content_identity. No derivable time means a
blocked projection, not a fabricated Web timestamp.

Accounting valuations take precedence, ordered by valuation_time then content_identity.
The replay fallback orders by final_state.last_effective_at then content_identity.
A replay with no effective time cannot be ranked safely and is blocked.

Views expose only the fields in the CTO projection contract. Decimal amounts use
fixed-point strings without insignificant trailing zeros, never floats or rounding.
The view identity hashes the actual JSON payload, including null fields, excluding
only view_identity. Request time, headers and collection input order do not influence
selection or response identity.

## Errors and sensitive data

Successful projection: 200, status OK (even if a domain status is BLOCKED).
Missing source: 404 / BLOCKED / ARTIFACT_NOT_AVAILABLE.
Malformed source: 409 / INVALID_WEB_ARTIFACT.
Tampering: 409 / ARTIFACT_INTEGRITY_FAILURE.
Incompatible links: 409 / WEB_STATE_INCONSISTENT.
Unsupported policy/schema: 409 / UNSUPPORTED_ARTIFACT_VERSION.
Sensitive data: 409 / SENSITIVE_DATA_DETECTED.
Unknown route: 404; unsupported method: 405.
Internal projection/programming defects remain 500; no global catch-all is installed.

Errors do not contain source dumps, exception text, headers or sensitive values.
The Observability sensitive-key catalogue is reused recursively, alongside obvious
credential-value markers. It is not an arbitrary-secret detection oracle. Complete
config, dataset, bar, Strategy/Risk and evidence payloads are never returned.

## Validation and deliberate deferrals

Unit tests cover malformed inputs, pin tampering, JSON identity, sensitive data,
collection ordering, HTTP surface and in-process network prohibition. The contract
builds real DATA -> Strategy -> Risk -> Backtest -> Accounting -> Observability facts
and combines them with Qualification + OPS for all six routes, including blocked LIVE.
No order/fill is fabricated by bypassing Risk in the new tests.

The existing module import guard now restores sys.modules after each re-import probe.
Without restoration, it replaced domain class identities for later contract tests.
The same network guard and assertions are preserved; no qualification case is changed.

Deferred: remote exposure/authentication, listeners, persistence, polling, WebSocket,
SSE, frontend/charting, infrastructure, configuration editing, trading controls and
all environment/OPS mutation. No server is launched by this mission.

## CTO review corrections — intrinsic integrity and public inspection

Readiness and qualification suite results now retain their content identity at
construction. Their canonical hash formula is unchanged for valid results. Inspection
recomputes this identity independently and compares it with the retained value.
Calling post-init on an existing object validates rather than overwrites its identity;
it cannot silently repair a tampered object before Web pinning.

Public read-only contracts now belong to the modules that own the artefacts:

- `ops.inspection.validate_readiness_result`: retained identity, config/check links,
  environment constraints and status/reason aggregation; no filesystem or startup.
- `ops.inspection.validate_health_result`: evidence identity and status compatibility.
- `test_qualification.inspection.validate_qualification_result`: retained suite hash,
  canonical case links, evidence references, aggregation and run ID; no evaluation.
- `accounting.inspection.validate_accounting_replay` and
  `validate_accounting_valuation`: canonical identities/IDs and supported consistency
  checks, using the owning module's existing contracts; no economic execution.
- `backtesting.inspection.backtest_causal_time`: verified result/input link and latest
  processed evaluation/order/fill time. Observability and Web use this same contract.

Web no longer imports cross-module private implementation names or encodes OPS
priority/Qualification aggregation. Domain policy/schema identifiers remain checked
for Web's UNSUPPORTED_ARTIFACT_VERSION mapping.

Regressions cover well-typed tampering before reference(), retained and arbitrary
identities, nested qualification alterations, every stored artefact identity, and a
rehashed readiness record whose config contradicts its environment. Pinning still
adds post-composition tamper detection; neither hashing nor pinning authenticates a
producer capable of replacing an entire consistent evidence chain.
