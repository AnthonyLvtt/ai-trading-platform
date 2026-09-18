# ENG-TO-OPS-005 — bounded read-only signal watcher

## Authority and lifetime

This implements the CTO bounded-watcher decision on base
`dc3f7b6f14f0de1a2a7c1ea3aec168f8869e99a5`.

The foreground watcher uses one TestnetActivationGrant, valid for at most 30 minutes.
The candidate is saved for external review. Its exact external pin is required before
any Strategy evaluation. A missing, malformed or different pin blocks the run.
Subsequent ticks retain that same grant and the pin received from the operator;
they never derive a pin from an artifact or renew a grant. Normal composition without
external pins remains blocked. No separate observation authority is introduced.

The deadline is the grant's absolute `validity_end`, not startup plus 30 minutes.
Starting eight minutes into its window leaves 22 minutes. Equality with the deadline
is expired. Expiry during sleep, a pin prompt, or a read terminates with
`BLOCKED / ACTIVATION_GRANT_EXPIRED`. The source is guarded before and after each GET.
An already in-flight GET may finish or time out, but its result is discarded after
expiry/stop and no further read or evaluation begins. Backwards UTC clock movement
fails closed. Domain clocks remain injected.

## Evaluation and scheduling

After initial approval the watcher evaluates the most recently closed candles once.
Subsequent evaluations are aligned to UTC five-minute boundaries, without catch-up
bursts. The existing DATA/Strategy/Risk pipeline remains authoritative:
BTCUSDT, final causal no-gap 5m candles, explicit SMA 20/50 and real account state.
Each tick revalidates activation/capability evidence, exact repository identity, and
rebuilds current market/portfolio/Strategy/Risk evidence. No cached Risk approval is
carried from an earlier tick.

- Real NO_ACTION or EXIT: report NOT_READY and wait for the next close.
- LONG_ENTRY: run the existing read-only portfolio, Risk, filter, price, capacity,
  quantity, OPS and Release checks. Only an admissible first-order candidate is
  presented for the second independently approved pin.
- Missing/wrong second pin or a gate failure: BLOCKED and stop.
- Both external pins and every final gate valid: READY_TO_SUBMIT and stop.

The quantity selection remains exact and mechanical under a projected cap of 5 USDT.
An impossible quantity remains blocked; there is no cap increase or forced BUY.
Candidate preflight never manufactures a trusted first-order receipt. The final
check still requires external first-order authority and repeats the temporal gates.
Waiting for a pin can make price/filter/open-order evidence stale. Existing final
freshness checks apply, including only the already-approved read-only open-order
refresh. Neither grants nor first-order candidates are silently regenerated.

## Local interface after CTO review and approved merge

The existing explicit CLI gains `--watch`, valid only together with `--check-only`.
Use the normal required source commit, release version, private session directory,
manual permission confirmation and external pin inputs. `--request-trust-pins`
allows terminal approval during the same process; those prompts are interruptible
and expire with the grant. `--watch --prepare` and `--watch --execute` are rejected.

Ctrl-C or SIGTERM requests a clean stop (`BLOCKED / WATCHER_STOPPED`). Sleeps use an
interruptible event. The terminal input helper runs only input in a daemon thread;
it has no credentials or economic capability. No background watcher service or
scheduler is installed. Restarting requires a new explicit session and external
review; serialized artifacts do not restore process-local authority.

Candidate files are written once, outside the repository. Repeated diagnostic proof
filenames are tick-scoped to avoid overwrite. Credentials remain in the existing
immutable provider snapshot and are removed from inherited environment before
build/qualification subprocesses. Logs/reports contain no credential material.

## Economic and regression boundaries

The only injected network source is the existing Testnet GET-only source. The only
order transport in the check-only composition refuses submission. The ledger remains
NOT_ATTEMPTED. No submit, cancel, retry, withdrawal, Live endpoint, automatic grant
renewal, implicit trust, Accounting mutation or new Observability EventType is added.
`REAL_ECONOMIC_CALLS = 0`; `LIVE = LIVE_FORBIDDEN`.

Offline tests use injected clocks and actual Strategy/Risk/filter logic over fixture
responses, with network and economic transport traps. They cover both external pins,
remaining lifetime, expiry at startup/between ticks/during prompts/during GET, clock
rollback, repeated NO_ACTION/EXIT, genuine crossover, existing exposure, stale price,
readiness, clean stop and no ledger consumption. Full validation and ATP/TQ remain
required. This mission opens a draft PR only, with no operational execution or merge.
