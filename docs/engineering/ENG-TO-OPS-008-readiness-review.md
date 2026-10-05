# ENG-TO-OPS-008 — First Testnet Order readiness review

This review is bound to clean `main` at
`185857815ab4abdd783a9edef39918f1c4bbb2be`. Its deterministic
[JSON artifact](ENG-TO-OPS-008-readiness-1858578.json) is `NO_GO` with reason
`FRESH_RUNTIME_EVIDENCE_REQUIRED`. It is a historical review of that exact
source, not an authorization for a later HEAD or a Testnet order.

The source inspection found the expected clean `main` and bound tree identity.
The canonical ledger at
`/Users/anthonylvtt/Library/Application Support/ATP/first-testnet-order/submission.sqlite`
had owner-only `0700` directory and `0600` file modes, no symlink in its path,
the expected schema and append-only triggers, SQLite `integrity_check = ok`,
and zero records. Its SHA-256 file digest was
`b1659a5c0da7ff35943929c077ec77be612836207e86a558452d880bd88ae9ed`
before and after the review. The audit opens it through SQLite `mode=ro` and
never provisions, resets, reserves or appends to the canonical campaign.

An offline Testnet qualification on that source passed with identity
`sha256:b9d60fe543f816128d30e48249daf35d3966c2ae79fab5643eabb67abe3439d8`.
The main-only Release producer qualified a manifest with identity
`sha256:31c418fe356d2cfce0c5da627761e6f4c5edcec3450909fe02313b9b479506bd`
and candidate ID
`release-candidate:sha256:922c37acc6c851580f3cde8c05d1900b3a502c2ce8b5cbd788da09f9e0b4ddae`.
The JSON artifact's `release_identity` denotes the manifest content identity;
neither a manifest file nor this review substitutes for the full same-session
ReleaseBundle and activation validation.

An explicit public-only pre-watch check was `FEASIBLE` for BTCUSDT at
`2026-10-05T17:02:52.000424+00:00`, with witness quantity `0.00007`, quote cap
`6`, and feasibility identity
`sha256:9a048f965b6d925fa7a0230080d84f902fbd1e6be64697212c112858b14d205b`.
That point-in-time price/filter evidence expires; it cannot be reused at a
future watcher tick or final gate.

Static contract inspection on the reviewed source confirms TESTNET / BTCUSDT /
SPOT / BUY MARKET, `FIRST_ORDER_QUOTE_CAP = Decimal("6")`, campaign-wide
`max_submissions = 1`, Risk V1 `max_positions = 1`, and no automatic retry,
resubmit or venue fallback. Preparation obtains private account/openOrders,
fresh filters and price before deterministic Risk and quantity selection.
The preparation watcher rechecks the empty ledger before each tick and stops
on a candidate before requesting the first-order pin. The separate economic
path retains the immediate pre-POST source/release and evidence boundary;
this review did not enter that path.

No fresh session-bound credential reference/capability, activation grant,
genuine LONG_ENTRY, Risk approval, private account/openOrders evidence or
first-order authorization candidate existed for this review. These identities
are deliberately `null` in the artifact. The existing
`--watch-prepare-first-order` path remains the only path to a candidate and
stops at `READY_FOR_CTO_REVIEW` before the first-order pin, ledger reservation
or execution. Every later run needs clean exact source, fresh Release/TQ,
capability and market/private evidence. A merge changes the source SHA and
requires a new review; no earlier identity or public observation is promoted.

Validation of this review branch: `make validate` passed with 1,722 tests,
Ruff and strict mypy. Ordinary tests used synthetic/injected transports and
made zero public/private network calls. The single public pre-watch GET sequence
above was an explicit operational check outside pytest. No credentials, grant,
pin, `ATTEMPT_STARTED`, economic call, cancel, withdrawal or Live access occurred.
`REAL_ECONOMIC_CALLS = 0`; `LIVE = LIVE_FORBIDDEN`.
