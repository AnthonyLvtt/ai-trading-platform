# ENG-TOR-001 — runtime credential attestation V1

Authority: CTO Runtime Credential Capability Attestation V1 decision.
Base: `95c4ab94761a4000e18f380dacae75771b47071a`.

## Two independent facts

The existing process environment provider establishes presence only. It reads
`ATP_BINANCE_TESTNET_API_KEY` and `ATP_BINANCE_TESTNET_API_SECRET` without defaults.
`ReferencedEnvironmentCredentialsProvider` loads once at construction and binds
an immutable in-memory pair to a non-secret
`CredentialReference` (`provider_type=PROCESS_ENVIRONMENT`, TESTNET only).
`new_credential_reference()` generates a local UUID without consulting secrets.
Environment changes never change the material attached to that reference. A
missing pair remains missing for that provider. `load()` returns a fresh redacted
transport container, so a caller cannot mutate the stored pair through it.
A process-local registry, protected by a lock, refuses reference ID reuse even
after a provider is discarded. Replacement requires a new reference/provider and
a newly pinned attestation. The registry contains only opaque IDs, no secret or
fingerprint. This is a session boundary, not a persisted cross-process registry;
restart requires fresh session composition and operator attestation.

A trusted operator separately verifies Binance Spot Testnet ownership, enabled
Spot trading, and absent/unavailable withdrawal. The sole accepted source is
`MANUAL_OUT_OF_BAND_TESTNET_PERMISSION_ATTESTATION`. If any fact cannot be
established, stop. This code does not perform that human verification or make
permission-probing network calls.

## Integrity and authority

`TestnetCredentialPermissionAttestation` contains the reference ID, explicit
permission facts, informative role, UTC verification/expiry times, schema version,
source and content identity. Lifetime must be positive and at most 24 hours;
validity is `verified_at <= current_time < valid_until`. Reattest on execution day.
`verified_by_role` is never authority. `TrustedCredentialPermissionAuthority`
requires an exact identity pin supplied by trusted session composition.

`RuntimeCredentialCapabilityAuthority` implements the existing public authority
contract. Each call revalidates intrinsic identities, exact provider/reference
binding, trusted pin, facts, lifetime and snapshot presence. Its clock is
explicitly injected. It returns the existing trusted capability model extended
with reference ID and permission-attestation identity. The authorization context's
validity window is intersected
with the permission attestation window so a sealed context cannot outlive it.
Legacy synthetic evidence remains supported by the existing test-only composition; no synthetic authority
is installed in runtime.

Failures expose only closed reason codes, including
`CREDENTIAL_REFERENCE_MISMATCH` and `CREDENTIAL_CAPABILITY_UNTRUSTED`. Expected
refusals propagate through the activation boundary without a broad exception
handler. Secrets never enter evidence identities or diagnostic representations.

## Explicit operator procedure and stop gates

1. Create a dedicated Testnet key out of band; do not send its values to ATP chat.
2. Inject credentials locally, generate an opaque reference, and construct the
   provider once to capture the pair for this session.
3. Verify permissions manually, then prepare the non-secret attestation with
   explicit UTC times and the same reference ID.
4. Inject its exact approved identity into the session permission authority.
5. Obtain capability evidence from the same session-bound provider.
6. Only after success collect the separately authorized read-only market/time
   evidence and return the Phase A report. STOP for CTO review.

No real attestation, pin, credentials or authority instance is shipped here.
Runtime files belong outside the repository; `/runtime/private/` is also ignored
as defense in depth. Do not include them in release/qualification/CI artifacts,
logs or Web. The module provides composition contracts, not an automatic CLI
trust loader or a permanent allowlist. A caller-supplied file alone is not trusted.

No first-order authorization is created. No check-only, submit, cancellation,
permission introspection, key administration, withdrawal or economic network
request is performed in this implementation. Live remains forbidden. Phase A
cannot be completed until the operator's real verification and local injection
exist. Existing grants, submission ledger, Accounting and order gates are unchanged.
