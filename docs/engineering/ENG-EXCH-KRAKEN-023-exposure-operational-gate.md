# ENG-EXCH-KRAKEN-023 — exposure observation operational gate

Status: **offline implementation prepared; real private observation not authorized**.

The sole prepared operator entry is `qualify_exposure_operator_gate`. Its inputs
are an exact reviewed `main` SHA, the approved account identity derived from an
IIBAN known independently to the operator, and injected credential and transport
providers. It is not wired to a command or OMS runtime path. No production
credential or private HTTP call is used in its tests.

After checking exact clean `main`, the gate loads one ephemeral credential. It
makes one exact `GetApiKeyInfo` request through the existing bounded read-only
transport. The response must identify the same API key and account IIBAN as
the locally loaded key and the preapproved account identity. The IIBAN is
normalized, domain-separated and hashed; its text is never placed in a result.
Missing or mismatched `apiKey` or `iban` fails before any exposure request.
The returned permissions must be exactly the existing least-privilege read-only
set. The resulting capability must remain fresh for the exposure runner.

The gate then invokes the existing bounded runner for exactly `BalanceEx` and
`TradeVolume`, in order, with the same credential, capability and derived
account identity. It checks source identity again after all three reads. The
result contains only fixed reason codes, identities, timestamps, the completed
route prefix and a total call count from zero to three. It contains no raw
response, API key, IIBAN, nonce or signature. Any missing `credit` or
`credit_used` field in `BalanceEx` still fails closed. Partial results never
qualify the source.

The account binding relies on Kraken's documented `GetApiKeyInfo` fields:
[`apiKey` and `iban`](https://docs.kraken.com/api/docs/rest-api/get-api-key-info).
The approved account identity must be established independently before running
the gate; deriving it from the just-returned response would remove the
cross-check. A simulated `PASSED` result proves the software sequence only.
It does not authorize or qualify a real observation, and cannot enable OMS
runtime `PASS`. A future read-only run requires separate explicit operational
authorization tied to an exact merged SHA and locally loaded credentials.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
