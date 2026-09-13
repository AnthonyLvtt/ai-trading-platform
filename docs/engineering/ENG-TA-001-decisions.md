# ENG-TA-001 — Testnet activation governance V1

Authority: CTO ENG-TA-001 mission and TRUST AUTHORITY & CREDENTIAL CAPABILITY V1
supplement. Base: `5b99b1df154aac91c0958dd09e597931e6d8412d`.

## Authority and final barrier

Qualification proves invariants, not permission. Credentials prove neither authority
nor capabilities. OPS readiness does not replace economic Risk approval. Release
promotion does not authorize runtime startup.

`ATP_TESTNET_ACTIVATION_V1 / 1.0` is immutable and revalidated. Its
`first_testnet_order_authorized` field is locked to false. No authority implementation,
production grant, production pin, credential attestation, selector or runtime bootstrap
ships in this mission. LIVE and withdrawals remain forbidden.

## Grant and trust

`TestnetActivationGrant` includes schema/policy versions, exact source commit/tree,
release candidate/manifest and TQ identities, explicit sorted unique symbol/order-type
sets, a UTC validity window and max_active_positions=1. Its grant ID is derived from
its content identity. No wildcard, ALL or ANY is accepted. The supplied CTO role is
only declarative.

`TrustedActivationAuthority` and `CredentialCapabilityAuthority` are abstract public
composition contracts. Only test files contain synthetic concrete implementations.
The grant authority must pin the exact grant identity and return matching source,
policy, release and TQ bindings. Structural validity never substitutes for this pin.

The second authority receives only an opaque credential source identity and returns
an attestation of presence, trading permission and absence of withdrawal capability.
No key material, permission probing or account request is involved. Missing/unknown
capabilities block; known withdrawal permission has its own absolute blocker.
Sensitive authority metadata is rejected without echo.

## Composition receipt

`validate_activation()` revalidates the release bundle against its wheel and source-bound
ATP evidence using the public Release inspector. It checks the TQ result against its
complete evidence bundle and exact source, including the reconciliation case. It verifies
both injected authority attestations before issuing a `RuntimeAuthorizationContext`.
The context binds both trusted evidence identities, source, release, ATP/TQ, credential
source, reconciliation, selected symbol/type and grant validity window.

A process-local weak receipt registry pins the exact context object and its original
identity. A copied/deserialized/manually constructed context is not authority, even with
identical hashes. Mutation invalidates the receipt. This is an in-process composition
boundary, not a cryptographic defense against arbitrary Python code execution. Private
receipt issuance belongs only to the activation module. No context authority is restored
from JSON. Restart requires a future explicit trusted composition.

All execution gates require an explicitly supplied UTC time. Window semantics are
`validity_start <= at < validity_end`; no wall-clock read enters a domain module. Risk
uses the Strategy logical evaluation time, and the final submission gate rechecks the
injected runtime time. Historical OPS inspection verifies a receipt's integrity and
bindings; it does not assert that its window is still current.

## Domain gates

- Risk keeps Shared's active environments unchanged. TESTNET is a conditional exception
  requiring a valid receipt matching the symbol and MARKET type. Strategy/market
  environment compatibility and all portfolio, Spot, long-only, margin/leverage and
  max-position checks still run. Risk provenance includes the context identity.
- OPS adds the critical `TESTNET_ACTIVATION_AUTHORIZED` check (ten checks total).
  TESTNET additionally requires ATP qualification matching the context, valid config,
  deterministic mode and validated Observability. Its policy distinguishes conditional
  TESTNET from unconditionally active environments. No grant remains BLOCKED.
- Release promotion accepts TESTNET only with a current receipt bound to the exact
  candidate/manifest and a revalidated wheel/bundle. Manifests distinguish unconditional
  allowed targets, conditional TESTNET and forbidden DRY_RUN/LIVE. The existing V1
  policies acquire explicit conditional activation metadata; their identities change.
  Conditional local deployment still requires the receipt and always records
  `process_started=false`. No deployment starts ATP or modifies OPS.
- `exchange.submission_gate.authorize_exchange_submission()` verifies the receipt,
  pinned grant, existing upstream order/Risk/Strategy contract, symbol/type scope,
  context-bound Risk approval, OPS READY, exact Release promotion and MARKET filters.
  It always returns BLOCKED/TESTNET_RUNTIME_BLOCKED after successful checks.
  Risk's context binding changes upstream order identity and hence deterministic client
  order ID when the grant changes. No quantity is inferred or adjusted.

The existing strict Exchange adapter cannot gain submission authority from the new OPS
readiness: its authorization path ends at TESTNET_RUNTIME_BLOCKED. The HTTP transport
also refuses every public SUBMIT operation before loading credentials. The existing
fake-only TEST harness stays in tests. Credential-format regressions exercise a fake
read-only authenticated query instead of bypassing the new economic barrier.

## Validation and deliberate deferrals

Tests cover the synthetic end-to-end chain and transport call count zero, expired and
future grants, wrong pins/source/release/TQ, forged contexts, wrong symbol/type,
missing/unknown/withdrawal capabilities, malformed runtime inputs, tampering, preserved
Risk economic rules, source-bound release/TQ evidence, local copy without startup and
secret-free results. No network is used by activation tests. Existing ATP and offline
TQ collectors remain separate from authorization.

Legacy tests are updated only for approved reason/check-count changes. The Backtest
no-financial-fields assertion now inspects field names instead of searching arbitrary
hex digests for the substring `fee` (which produced a false failure after identity changes).

No Web route or Observability EventType is added. No signing/PKI, grant persistence,
permission probing, credential loading changes, remote deployment, reconciliation loop,
order retry, sizing, Testnet order/cancellation or Live behavior is introduced.
ENG-TO-001 must separately authorize the exact production grant, both authority
compositions, credential attestation, time/symbol scope and the first economic request.
