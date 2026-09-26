# ENG-TO-OPS-008E — long-horizon preparation watch

Base: `a56afbf9997c29125ebd48fe44ae18b6d2005ebe`.

## Explicit duration contract

`--activation-grant-duration-minutes` is accepted only with
`--watch-prepare-first-order`. Omission retains the existing 30-minute grant. The
inclusive input range is one through 720 minutes. Every other CLI mode rejects the
option.

The requested duration is part of candidate construction, but the effective
`validity_end` is the earlier of the requested end and the current session-bound
credential permission's `permission_valid_until`. The resulting start and end are
therefore covered by the activation grant's content identity and its exact external
pin. Missing permission lifetime blocks long-horizon preparation.

The watcher binds the grant against the duration explicitly selected for that run.
It retains the same grant, runtime authorization content and credential reference
for every evaluation. Expiry is final: the run returns
`ACTIVATION_GRANT_EXPIRED` without producing or requesting a replacement grant.

## Preserved boundaries

The option does not apply to check-only watch, prepare or execute. Those paths keep
their 30-minute policy. The long-horizon mode remains foreground, interruptible and
TESTNET-only. Every tick retains the before/after GET deadline checks, monotonic UTC
clock, source/release binding, credential capability checks and fresh evidence.

`NO_ACTION` and `EXIT` may repeat until the one grant expires. A genuine
`LONG_ENTRY` may build and save the exact first-order authorization candidate, then
must stop at `READY_FOR_CTO_REVIEW`. The mode has no first-order pin, trust receipt,
execution call or economic transport. It cannot append to the canonical ledger.

`REAL_ECONOMIC_CALLS = 0`

`LIVE = LIVE_FORBIDDEN`
