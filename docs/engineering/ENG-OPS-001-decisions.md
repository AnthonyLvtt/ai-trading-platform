# ENG-OPS-001 — Operational Safety Foundation

## Authority

The CTO `ENG-OPS-001 V1 OPERATIONAL SAFETY POLICY` substitutes for the absent
SPEC-OPS-001. Base: ce636e4cace7aec6a20fde0c899f5fb857303f0a.

Health is not readiness; operational readiness and qualification are not trading
authorization. No OPS call creates a Risk approval, order, fill or Accounting
mutation, enables Exchange, or authorizes release. Policy ATP_OPS_V1 / 1.0 is
locked at construction and revalidated at readiness boundaries.

## Environments

| Environment | Active | Qualification |
| --- | --- | --- |
| LOCAL, TEST | Yes | NOT_REQUIRED |
| BACKTEST, SIMULATION | Yes | Exact V1 PASSED evidence required |
| DRY_RUN | No — ENVIRONMENT_INACTIVE | Not evaluated |
| TESTNET | No — TESTNET_NOT_AUTHORIZED | Not evaluated |
| LIVE | No — LIVE_FORBIDDEN | Not evaluated |
| Unknown | No — UNKNOWN_ENVIRONMENT | Not evaluated |

The shared Environment enum is reused as OperationalEnvironment. No normalization
or favorable fallback is applied to unknown inputs. Credential presence and flags
cannot change this table. Health is evaluated independently from environment.

## Configuration and filesystem

Readiness accepts an explicit dictionary with every normative config field, or a
previously returned OperationalConfig. Missing keys do not acquire defaults.
ATP / config schema 1.0, observability_enabled=true, deterministic_mode=true and
qualification_required matching the environment matrix are enforced.

The canonical OperationalConfig is returned after path resolution, with its stored
content identity. A typed config is checked against its stored identity before
use. The readiness identity includes the canonical configuration identity.

FilesystemBoundary is explicit and injectable. The default LocalFilesystem only
resolves paths and checks existing directories; it creates nothing. An injected
boundary can attest that a missing artifacts directory is creatable. A relative
workspace is resolved in that boundary's local context; a relative artifacts path
is resolved under the canonical workspace. Returned resolved paths must be absolute
and contain no parent traversal. URI/network paths are refused. Resolution happens
before containment checking, including symlink targets. Existence and creatability
are observations at check time, not a guarantee against later filesystem changes.

## Startup and aggregation

The nine closed StartupCheck types are all critical. Every result records status,
reason, and optional evidence identity. Missing/invalid prerequisites block; there
is no best effort startup. QUALIFICATION_VALID in LOCAL/TEST is PASSED/NOT_REQUIRED.
For inactive environments its result is BLOCKED/STARTUP_CHECK_FAILED without
examining qualification. A READY result requires all nine checks to pass.

The global reason follows exactly the CTO priority list, beginning with
LIVE_FORBIDDEN and TESTNET_NOT_AUTHORIZED. Full check details are retained.
Result hashing sorts checks by enum value; arbitrary collection order cannot change
identity. No execution timestamp participates.

## Qualification integrity

OPS accepts the permitted typed equivalent, QualificationReference. It carries the
original QualificationSuiteResult, the producer's expected content identity and
qualification_run_id. These must be pinned when the qualification evidence is
produced or read from its trusted manifest, not refreshed after mutation.

This anchor is necessary because the accepted QualificationSuiteResult exposes
computed identity properties: reading them again after alteration would silently
produce new identities. OPS compares the received result against the pinned
identity/run ID. It additionally checks exact suite/policy, all ten canonical case
definitions, PASSED/reason consistency, evidence counts, ordering, uniqueness and
identity types. It never calls evaluate_case/evaluate_suite or changes evidence.
A bare suite result without an independent pinned reference is rejected when
qualification is required. Content integrity does not authenticate the producer;
this mission adds no signatures or external trust service.

## Observability evidence

The local helper inspects the existing event validation, audit validation,
diagnostic record contract and sensitive-key guard. No event is emitted.
Both schema versions must equal the accepted Observability schema. Stored evidence
identity and all explicitly named boolean capabilities are verified.

The CTO text says “five capabilities” but enumerates four booleans: event identity
validation, audit chain validation, diagnostic logging, sensitive-data guard.
The implementation enforces those four plus the two schema versions; no unnamed
fifth capability is invented. A schema/payload expansion would require CTO direction.
No new EventType is added; OPS does not emit logs or raw exception text in V1.

## Health and shutdown

OperationalHealthEvidence is independently hashed and validated. A valid fatal
flag yields UNHEALTHY, missing/invalid evidence yields UNKNOWN, and all positive
conditions with no fatal flag yield HEALTHY. HEALTHY + LIVE remains BLOCKED.

Shutdown is a pure one-step transition: STARTING/READY/BLOCKED → STOPPING,
STOPPING → STOPPED. STOPPED has no outgoing shutdown transition. Valid requests
must match their stored identity and use closed reason/state enums. Invalid input
returns BLOCKED/INVALID_SHUTDOWN_REQUEST without inventing a state. Reasons include
explicit qualification invalidation and observability failure; there is no polling,
background monitor, OS signal handler or auto-restart.

## Security and runtime boundaries

Reuse Observability SENSITIVE_KEYS, extending only with the two OPS-specific
forbidden fields exchange_credentials and withdrawal_address. Scan keys recursively
without case sensitivity; reject recognizable credential markers in values too.
Contaminated config is not returned or logged, and only a type-based fallback
identity is retained. This cannot identify every arbitrary unknown secret value;
callers must never put credentials into nominally non-secret fields.

External record parsing catches only expected malformed-input exceptions; filesystem
errors are contained at the injected filesystem boundary. No giant Exception catch
covers the engine. Fallbacks use type module/qualified name, never arbitrary str/repr.
The core checks do not import domain execution authorities or call the network.

## Validation and deferrals

Unit tests cover environment matrix, missing/mutated proof, canonical identities,
symlink escape, explicit creatability, credentials, health/readiness separation and
shutdown. Contract tests obtain a real QualificationSuiteResult from the qualification
engine using explicit test evidence, then combine it with local Observability
readiness. They execute no trading. Static tests guard OPS authority and network
boundaries. The ten qualification cases and their collector mapping remain unchanged.

No daemon, scheduler, cloud deployment, systemd, container orchestration, remote
logs/metrics, Exchange/Testnet/Live connection or release automation is added.
