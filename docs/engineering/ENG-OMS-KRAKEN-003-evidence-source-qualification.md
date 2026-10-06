# ENG-OMS-KRAKEN-003 — Exposure evidence source qualification

Status: **offline source contracts accepted; runtime PASS still unqualified**.

Base: `918e72ad17e60b8205dd6150bec19568f807f0b7`.

## CTO qualification decision

The OMS exposure layer now has an explicit source registry and freshness policy
for the evidence required by the accepted 10% BTC/EUR BUY LIMIT rule.

The accepted source contracts are:

- account balance: Kraken private `/0/private/Balance`, already qualified for
  read-only observation;
- account-wide open orders: Kraken private `/0/private/OpenOrders`, already
  qualified for read-only observation;
- valuation price: Kraken public `/0/public/Ticker`, already qualified for
  read-only observation;
- spendable EUR: Kraken `/0/private/BalanceEx`, **not observation-qualified
  and not qualified as an exposure evidence source**;
- bounded fee: an explicit Kraken Spot fee-schedule source, **not yet
  observation-qualified and not qualified as an exposure evidence source**.

No new route is added to any transport or signing allowlist. This registry
grants no network-call authority; existing read-only transport authority remains
governed by its own qualification and operational controls.

## Freshness policy V1

At the future OMS assessment instant:

- spendable EUR evidence: maximum age **30 seconds**;
- account balance evidence: maximum age **30 seconds**;
- complete account-wide open-orders evidence: maximum age **30 seconds**;
- BTC/EUR valuation price: maximum age **10 seconds**;
- bounded fee evidence: maximum source age **24 hours**;
- maximum observation-time skew across the exposure evidence bundle:
  **30 seconds**.

These are authorization limits, not cache TTLs. Equality at the age limit is
permitted by the existing `FreshnessEvidence` contract; older evidence blocks.
Unknown or contradictory timestamps block.

## Authority boundary

`EXPOSURE_EVIDENCE_QUALIFICATION_V1` qualifies the offline contracts and
records the exact remaining blockers. It deliberately reports
`runtime_pass_qualified = False` because spendable-EUR and bounded-fee sources
are still unqualified.

A later mission may qualify those source producers. Only after both blockers
are removed may an OMS exposure assessment capable of returning PASS be
implemented.

Kraken-only, Spot BTC/EUR, offline/fail-closed.
`REAL_ECONOMIC_CALLS = 0`; `LIVE = LIVE_FORBIDDEN`.
