# ENG-REL-001 — Release and local deployment V1

Authority: CTO V1 decision supplied for this mission; base
`979f887e80758a49044d63b345f2145259e1ad7b`. No SPEC-REL-001 is present.

Build success is not release authorization. Qualification is not promotion.
Local installation does not start trading. OPS and Risk remain unchanged;
Exchange code may be packaged but is never invoked.

## Policy and source binding

`ATP_RELEASE_V1 / 1.0` is locked at construction and revalidated at use.
Only a clean `main` source is admissible. The source inspector records HEAD,
Git tree SHA, and a canonical sorted inventory of every tracked path, mode and
SHA-256 content identity. Missing files and symlinks fail closed. Untracked
non-ignored files make the checkout dirty.

The Qualification collector now binds every subject to that complete inventory
identity and HEAD, checking the source before and after collection. The ten
qualification cases and their invariant semantics remain unchanged. Release
verifies the stored result identity, run ID, policy, and explicit subject/evidence
chain with the public qualification evaluator. This is integrity verification,
not a rerun of tests or collection. Replacing only the envelope's source label
cannot substitute a qualification from another tree.

Validation evidence separately records lint, typing, tests and lock checking.
The release driver executes canonical validation and qualification itself.
Externally supplied evidence remains an explicit local trust boundary, not a
signed external attestation. No cryptographic identity proves that an arbitrary
caller actually ran a command; the canonical producer establishes that fact.

## Wheel and manifest

The producer builds a Python wheel from a temporary copy containing only the
qualified tracked bytes and modes. Ignored local files cannot enter the build.
`SOURCE_DATE_EPOCH=315532800` stabilizes archive times. The binary wheel SHA-256
is recorded and checked again before promotion and deployment. ZIP members and
all wheel RECORD hashes/sizes are verified without executing package code.

The release label is supplied explicitly: nonempty ASCII, at most 64 characters;
it does not rewrite the package version from the exact source pyproject.

`release-manifest.json` is canonical UTF-8 JSON. Schema `1.0` contains:
policy ID/version, candidate ID, explicit version, source commit/branch,
repository and lockfile identities, wheel filename/SHA-256, qualification
suite/version/result identity/run ID, allowed and forbidden promotion targets,
`auto_start_allowed=false`, and `content_identity`. Its identity covers all
other fields. Candidate, manifest and retained evidence are checked together.
A changed proof, manifest or artifact invalidates promotion.

## Promotion and installation

| Target | Decision |
| --- | --- |
| LOCAL / TEST / BACKTEST / SIMULATION | ALLOWED after integrity verification |
| DRY_RUN | BLOCKED / PROMOTION_TARGET_FORBIDDEN |
| TESTNET | BLOCKED / TESTNET_NOT_AUTHORIZED |
| LIVE | BLOCKED / LIVE_FORBIDDEN |

No override exists. A blocked target never mutates the candidate.

`COPY_ARTIFACT` copies a verified wheel. `PYTHON_WHEEL_INSTALL` additionally
uses `uv --no-config --offline pip install --no-index --no-deps --target`.
Dependencies are not fetched or resolved; this is local package installation,
not a claim that a runnable environment is ready. Neither mode imports/starts
ATP. The destination is explicit, canonical and local, must not already exist,
and its parent must exist. Staging precedes exclusive destination creation;
existing runtime configuration is never overwritten. Network mounts cannot be
identified from path syntax alone; callers must provide a local filesystem.

Deployment records always contain `process_started=false`. No OPS readiness,
Risk authorization, daemon, signal handler or automatic rollback is produced.

## CI and usage

The sole new workflow is `.github/workflows/release.yml`, manually dispatched
on main with an exact source commit and explicit release version. It verifies
HEAD equality, frozen dependencies, validation, qualification and build, then
uploads the wheel, manifest and qualification JSON as CI artifacts. Upload is
not deployment or publication. No application/Exchange secrets are referenced.

Local equivalent, from clean main:

```bash
UV_CACHE_DIR=/tmp/atp-uv-cache uv run python scripts/release.py \
  --source-commit <exact-main-sha> --version internal-001 \
  --output /absolute/local/output-outside-checkout
```

Output must be outside the checkout and absent. This prevents output from
changing the qualified source. The main-only producer deliberately refuses the
implementation branch; unit/contract fixtures exercise its domain contracts
without pretending that a feature branch is release-authorized.

## Security and deferrals

The shared sensitive-key policy is reused with `exchange_secret` and
`withdrawal`. Contaminated metadata is blocked without echoing values. Public
decision boundaries reject malformed types and stored-identity tampering.
Diagnostics contain only closed reason codes, not captured subprocess output.

No remote deployment, SSH, registry/PyPI publication, GitHub Release publication,
credentials, trading bootstrap, Testnet/Live activation, container orchestration,
auto-restart or auto-rollback. The release workflow itself is reviewed in draft;
it cannot be dispatched from main until the CTO approves and merges this work.
