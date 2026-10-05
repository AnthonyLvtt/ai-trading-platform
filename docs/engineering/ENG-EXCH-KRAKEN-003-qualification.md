# ENG-EXCH-KRAKEN-003 — Kraken private connectivity qualification

`PRIVATE_CONNECTIVITY` is a separate, non-economic qualification result. It
does not replace or change `OFFLINE_CONTRACT`, and it grants no Risk, OMS,
order, funding, withdrawal, or Live authority.

The operator command is deliberately interactive:

```text
python scripts/qualify_kraken_private.py \
  --level PRIVATE_CONNECTIVITY \
  --expected-source-sha <exact-approved-main-sha>
```

Before either masked credential prompt appears, ATP requires a clean worktree,
branch `main`, and a HEAD equal to the expected SHA. The API key and secret are
read with `getpass`, never accepted through arguments, environment variables,
or files, and retained only in mutable process-local buffers. ATP derives an
opaque `Environment.LOCAL` credential reference from the API key. Credential
material never enters evidence or the serialized qualification result. The
buffers are overwritten when the run ends; Python cannot guarantee removal of
all interpreter-managed copies.

The real transport exposes no free host, method, path, or parameter API. It is
fixed to `api.kraken.com:443`, POST, a ten-second timeout, a 2,000,000-byte
response limit, the platform trust store, and TLS 1.2 or newer. Its complete
private route vocabulary is:

- `/0/private/GetApiKeyInfo`
- `/0/private/Balance`
- `/0/private/OpenOrders`

Each request body contains only `nonce`. Redirects, retries, proxies, venue
fallback, route fallback, and parameterized OpenOrders are absent. A
process-local per-credential nonce provider is sufficient only for this
single-process explicit command and does not claim cross-process safety.

The qualification performs exactly three sequential observations. It first
requires an authenticated GetApiKeyInfo response and reduces that response to
the existing least-privilege capability evidence. The returned `apiKey` metadata
is not required to be a literal echo of the locally supplied API key. It then
constructs the existing Balance evidence and the exact request-bound,
account-wide OpenOrders evidence. Raw responses, auth headers, nonce values, key
metadata, IP allowlists, and secrets are excluded from the result. Failures
produce only fixed reason codes, with no retry.

A `PASSED` result requires all of the following:

- the final source tree equals the initial source tree exactly;
- the existing capability, balance, request, and open-order evidence validate;
- `private_network_calls = 3` in the exact route order above;
- `real_economic_calls = 0`;
- `side_effect_performed = false`;
- `live = LIVE_FORBIDDEN`.

Normal pytest and CI use injected transports and make zero private network
calls. A real `PRIVATE_CONNECTIVITY` run requires separate CTO authorization
against an exact merged `main` SHA.

## Operational closeout — ENG-EXCH-KRAKEN-003/004/005

The CTO accepted the real, read-only `PRIVATE_CONNECTIVITY` qualification on
canonical `main` `02aed8705ba342c79edb7b21ae4a76402739dfd5` after PR #34
merged and post-merge Quality #85 succeeded. The qualified run returned
`PASSED` after exactly one call each to `GetApiKeyInfo`, `Balance`, and
`OpenOrders`: `private_network_calls = 3`, `real_economic_calls = 0`,
`side_effect_performed = false`, and `live = LIVE_FORBIDDEN`.

ENG-EXCH-KRAKEN-003/004/005 are closed for private read-only connectivity.
This closeout grants no additional Kraken capability and authorizes no further
real private calls. AddOrder, CancelOrder, funding, withdrawal, leverage,
Futures/Margin, Live, automatic retry, and venue fallback remain forbidden.
Any extension requires a new explicit CTO mission.
