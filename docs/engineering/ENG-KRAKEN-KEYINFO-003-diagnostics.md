# ENG-KRAKEN-KEYINFO-003 — sanitized key-info diagnostics

Baseline: `d1abc33e7b2ba125fae09e507783b8ee1e209c5e`.

## Investigation

The comparison with `ce068baca8df94f09181fd060f746193498d2598`
found no change to `GetApiKeyInfo`, its private transport, its parser or its
binding checks. PR #66 added only the explicit `EUR_BALANCE_NOT_OBSERVED`
branch after exposure connectivity. It did not introduce the observed
key-info rejection.

The old gate reduced transport failures, API rejections, malformed envelopes,
invalid timestamps and malformed permission evidence to
`EXPOSURE_GATE_KEY_INFO_INVALID`. The sanitized result from the reported run
therefore cannot establish which one occurred, and the raw private response is
neither retained nor required for diagnosis.

The investigation did reproduce one independent defect in the key-info time
check. Its 30-second window started before the interactive credential loader.
An operator taking more than 30 seconds to enter credentials caused a fresh
one-second HTTP observation to be rejected as stale. The transport already
records the timestamp immediately before the request, so freshness is now
measured from `request_started_at` to `observed_at`. Reversed, timezone-naive or
longer-than-30-second observations still fail closed.

## Sanitized outcomes

The gate now distinguishes these non-secret categories:

- transport failure;
- Kraken API rejection;
- invalid response shape or route;
- invalid or stale timing;
- invalid permission evidence;
- insufficient least-privilege permissions;
- existing credential, API-key and account-binding failures.

No Kraken error text, response payload, API key, secret, IIBAN or financial
amount is copied into the result. A rejected or malformed response never
produces capability evidence, and `BalanceEx` is not called after any key-info
failure.

All coverage uses deterministic synthetic transports. Existing coverage for a
valid response, account mismatch, extra permissions and missing EUR remains in
place. The new coverage adds API rejection, malformed response, malformed
permission evidence, transport failure, request staleness and delayed
interactive entry with a fresh response.

No private Kraken request is performed by this change. `REAL_ECONOMIC_CALLS =
0`; `runtime_pass_qualified = False`; `side_effect_performed = False`; `LIVE =
LIVE_FORBIDDEN`.
