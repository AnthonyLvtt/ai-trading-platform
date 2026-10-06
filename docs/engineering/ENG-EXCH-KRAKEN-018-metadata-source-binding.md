# ENG-EXCH-KRAKEN-018 — Bind metadata to its mapping source

## Base and motivation

Base: `672d94e7e8d0c8b40e8a5c5b90f9aa99559baa7a`.
Post-merge Quality #170 succeeded on this exact base.

The Kraken AssetPairs parser creates the BTC/EUR mapping and instrument
metadata from one public response. The mapping records that response in
`metadata_identity`, and the metadata records it in `source_identity`.
Preflight previously checked the metadata's mapping identity and observation
time, but did not compare these two raw-response identities. Constraints from
a different response could therefore be mixed with an otherwise valid mapping.

## Scope

- Require `metadata.source_identity == mapping.metadata_identity` after both
  records pass their complete integrity checks.
- Apply the same rule in `build` and `validate_against` through the shared
  evidence validation path.
- Cover limit and market orders with a second, internally consistent metadata
  record that has a different public source identity.

## Boundary

Matching source identities prove a declared same-source relationship, not
authenticity of the public response or current exchange state. A caller can
still fabricate self-consistent evidence. This does not authorize submission.

No credential, nonce, signature, HTTP/socket transport, retry, economic call,
or Live capability is added. Kraken-only, Spot BTC/EUR, offline and
fail-closed. `REAL_ECONOMIC_CALLS = 0`. `LIVE = LIVE_FORBIDDEN`.
