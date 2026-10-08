# ENG-EXCH-KRAKEN-023 — exposure observation operational gate

Status: **read-only gate implemented; financial qualification incomplete; no economic authority**.

The operator gate `qualify_exposure_operator_gate` binds an exact reviewed
source SHA, independently approved account identity, ephemeral credential,
least-privilege capability and bounded private observations.

The gate first performs one exact `GetApiKeyInfo` read and verifies account,
credential and permission bindings. Authentication success and permission
validation are prerequisites only; they are not financial qualification.

The exposure stage begins with `BalanceEx`.

A full financial path may continue to `TradeVolume` only when BalanceEx produces
a valid numeric spendable-EUR proof.

A syntactically valid BalanceEx response with no EUR row is now represented as:

```text
status = INCOMPLETE
reason_code = EXPOSURE_GATE_EUR_BALANCE_NOT_OBSERVED
completed_routes = [
  "/0/private/GetApiKeyInfo",
  "/0/private/BalanceEx"
]
total_private_network_calls = 2
runtime_pass_qualified = false
TradeVolume = not executed
```

Absence of the EUR row is not interpreted as zero and does not qualify financial
exposure.

Malformed rows, missing required fields, unsupported assets and other invalid
evidence remain failures. The specialized sanitized diagnostics introduced by
PR #65 remain available for malformed BalanceEx shapes, including:

- `EXPOSURE_GATE_BALANCE_EUR_MISSING` for the pre-ENG-KRAKEN-EXPOSURE-002
  missing-EUR failure behavior preserved in historical evidence;
- `EXPOSURE_GATE_BALANCE_FIELDS_INCOMPLETE`;
- `EXPOSURE_GATE_BALANCE_ASSET_UNSUPPORTED`;
- the generic `EXPOSURE_GATE_EXPOSURE_EVIDENCE_INVALID` where still applicable.

## Historical real read-only attempt — 2026-10-08

Against baseline:

```text
ce068baca8df94f09181fd060f746193498d2598
```

the authorized bounded read-only attempt produced the historical result:

```text
status = FAILED
reason_code = EXPOSURE_GATE_BALANCE_EUR_MISSING
runtime_pass_qualified = false
completed_routes = ["/0/private/GetApiKeyInfo"]
total_private_network_calls = 2
real_economic_calls = 0
side_effect_performed = false
LIVE = LIVE_FORBIDDEN
```

`BalanceEx` was requested but did not produce qualified financial evidence.
`TradeVolume` was not executed.

This historical failure is retained as evidence of the then-current contract. It
is not rewritten after ENG-KRAKEN-EXPOSURE-002.

The account binding may use an independently known IIBAN only to derive the
approved opaque account identity. Raw IIBAN text, API keys, secrets, signatures,
nonces, private payloads and real financial amounts must not appear in persisted
results or documentation examples.

Connectivity, least-privilege permissions, HTTP success, route observation,
financial evidence, operational qualification and real-trading authorization
remain separate gates.

`REAL_ECONOMIC_CALLS = 0`; `runtime_pass_qualified = False`;
`LIVE = LIVE_FORBIDDEN`.
