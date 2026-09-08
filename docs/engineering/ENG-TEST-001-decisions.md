# ENG-TEST-001 — Test & Qualification Foundation

## Authority and scope

The CTO decision `ENG-TEST-001 V1 TEST & QUALIFICATION POLICY` is normative while
SPEC-TEST-001 is absent. Base: `2c3a2d9b60e81930da56da5ce75144b6a17b49c9`.

Tests exercise behavior; qualification explicitly evaluates accepted invariants
against evidence. A green pytest run or CI alone is not qualification. A PASSED
qualification is never release, trading, or Live authorization.

## Policy and catalogue

`ATP_QUALIFICATION_V1 / 1.0` is locked at construction and revalidated at each
public engine entry. The suite is `ATP_V1_QUALIFICATION / 1.0`.

| Case | Accepted invariant family |
| --- | --- |
| Q-FOUNDATION-001 | Python, lock, lint, types, tests, repository structure |
| Q-SHARED-001 | Canonical serialization, identities, typed IDs, UTC, environments |
| Q-DATA-001 | Historical admissibility, finality, gaps, causal availability, lineage |
| Q-STRATEGY-001 | Determinism, causal DATA, invalid input rejection, proposal only |
| Q-RISK-001 | Malformed evidence, portfolio limit, market policy, environment, symbol |
| Q-BACKTEST-001 | Risk gate, next open, no skipping, no future state, end of replay |
| Q-ACCOUNTING-001 | External quantity, Decimal, ledger and replay integrity, equity |
| Q-OBS-001 | EVENT != LOG, payloads, hash chain, tampering, causal provenance |
| Q-BOUNDARY-001 | No prohibited authority imports in accepted modules |
| Q-SECURITY-001 | No secrets, withdrawal, Live or favorable environment fallback |

Each family has a closed list of named checks in `catalogue.py`. These are stable
qualification identities independent of pytest function names. A definition that
weakens this list is rejected. The local producer maps each check to explicit
executable probes in `scripts/qualify.py`; it does not infer a pass from a global
exit code for domain cases.

## Evidence and source manifests

Evidence contains one typed observation: `satisfied`, `applicable`, `reproducible`
are strictly booleans, plus a nonempty `observation_id`. Its immutable canonical
payload is stored as bytes. Each evidence item references an explicit source
manifest, supplied locally, by subject ID and SHA-256 identity. The engine checks
that the manifest contains the exact case/check observation and that source
module and producer match. Missing manifests block. Modified evidence hashes,
modified manifest content, unknown policies, duplicate IDs, and noncanonical
JSON block before any PASS evaluation.

Source manifests can carry minimal domain artifact identities and test references.
They never embed complete domain objects or raw diagnostic logs. Test observations
are evidence from their explicit producer; hashes prove content integrity, not
independent producer authentication. V1 has no signatures or external trust service.
The accepted producer and its criterion-to-test mapping remain reviewable code.

The local collector includes a content identity of source/test/script files plus
pyproject.toml, uv.lock, and Makefile. Evidence references include test node IDs and
setup/call/teardown outcomes. Missing, skipped, interrupted, or setup/teardown-error
probes cannot produce a reproducible passing observation. Domain assertion failures
produce sufficient negative evidence. Foundation records explicit command exit
codes and Python version rather than a textual CI claim.

## Results and aggregation

- PASSED: every check has valid, reproducible, applicable evidence and is satisfied.
- FAILED: complete usable evidence establishes an invariant violation.
- BLOCKED: missing, invalid, incompatible, non-reproducible or contaminated evidence.

`CASE_NOT_APPLICABLE` is BLOCKED. Missing cases give SUITE_INCOMPLETE. Repeated case
or evidence IDs block; there is no deduplication. Suite precedence is
BLOCKED > FAILED > PASSED. Within the selected status, the first case in canonical
case-ID order supplies the suite reason; all individual results remain available.
Evidence is sorted by stable evidence ID and case results by stable case ID.

Result identities include definitions, policy identity and evaluated evidence
identities. Run IDs derive from the suite result identity. No clock is read.
Public engine functions catch malformed external evidence without str/repr calls.
Unsafe inputs are not echoed; invalid case fallback identities use the type name.
Suite evaluation rebuilds case results from evidence rather than trusting caller
supplied PASSED results.

## Security and boundaries

The sensitive-key set is imported from Observability. Keys are checked recursively
and recognizable credential markers are also blocked. Contaminated evidence
returns only SECRET_MATERIAL_DETECTED without copying the value or evidence IDs.
This is not a general detector of all possible unknown secrets; producers must not
include credential values, arbitrary notes, or raw logs in manifests.

The runtime only reads qualification records. No orders, fills, Risk approvals,
Accounting mutation, network, system clock, or Exchange calls. The local producer
runs validation tooling outside the runtime package. Existing domain behavior is
unchanged; the new vertical contract uses real DATA→Strategy→Risk authorization
before creating simulated execution through the accepted Backtest engine.

## Validation and evidence collection

Unit tests exercise qualification semantics and malformed/tampered input. Contract
tests bind real vertical-slice identities to qualification evidence. Qualification
probes are in `tests/qualification`, distinct from `tests/unit` and `tests/contract`.
No infrastructure integration suite is needed by this in-memory scope.

Run canonical validation as instructed, then explicitly collect qualification:

```bash
python --version
UV_CACHE_DIR=/tmp/atp-uv-cache uv lock --check
UV_CACHE_DIR=/tmp/atp-uv-cache uv sync --dev --frozen
UV_CACHE_DIR=/tmp/atp-uv-cache make validate
UV_CACHE_DIR=/tmp/atp-uv-cache uv run python scripts/qualify.py
```

The collector writes `eng-test-001-qualification.json` locally (not committed).
Repeat collection with identical files and probe outcomes to reproduce the same
qualification identity. No duration or execution timestamp participates.

## Deliberate deferrals

No release/deployment automation, Testnet/Live testing, remote artifacts, cloud
test farm, mutation platform, chaos infrastructure, benchmark/performance lab,
coverage threshold, or new economic semantics. These artifacts authorize no release.
