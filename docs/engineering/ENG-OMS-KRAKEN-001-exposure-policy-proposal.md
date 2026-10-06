# ENG-OMS-KRAKEN-001 — Proposed offline entry exposure policy

Status: **DRAFT — CTO decision required; no runtime authority**.
Base: `7cec60045095c6e020e554fe5255209a1fd0e89b` (post-merge Quality #172 succeeded).

## Decision requested

The CIO has specified a maximum **10% of portfolio value for each BTC/EUR BUY
entry**. This document proposes the exact fail-closed interpretation for CTO
review. It does not amend the accepted Risk V1 policy, which deliberately has
no order sizing, and it does not enable an order path.

## Proposed rule

For a candidate Kraken Spot BTC/EUR BUY limit order, define:

- `portfolio_value_eur = total_EUR + total_BTC × valuation_price_eur_per_btc`;
- `entry_cap_eur = portfolio_value_eur × 0.10`;
- `worst_case_order_cost_eur = quantity_BTC × limit_price_eur_per_btc +
  bounded_fee_eur`.

The candidate may pass this *exposure assessment* only when
`worst_case_order_cost_eur <= entry_cap_eur` **and**
`worst_case_order_cost_eur <= spendable_EUR`. Equality is allowed. A passed
assessment remains neither a Risk approval nor submission authority. Existing
Strategy, Risk, order-intent, preflight, and ledger gates still apply.

The first version should accept BUY **limit orders only**. A market order has
no deterministic maximum execution price in the current contracts, so its
worst-case EUR cost cannot be bounded here. It remains blocked until a separate
price-collar and fee policy is accepted. A SELL/EXIT is not an entry and should
be governed by a separate position-reduction rule, including proof of free BTC;
the 10% entry cap must not prevent a complete exit.

## Required evidence before implementation can return PASS

- One complete BTC/EUR portfolio snapshot with both EUR and BTC total balances,
  bound to one account, credential reference, observation time, and source.
- A qualified, independently identified **spendable EUR** amount that accounts
  for funds reserved by open orders. Existing `AccountBalanceEvidence` records
  amounts but does not attest spendable funds. Existing open-order evidence does
  not contain enough price/reservation data to derive the missing amount.
- A positive finite BTC/EUR valuation price with accepted freshness and source
  binding, plus a bounded fee amount or fee-rate policy for the candidate.
- Complete account-wide open-order evidence, with no unresolved order that
  could consume the proposed budget, and an approved Risk decision for the
  same candidate. Any unknown, stale, contradictory, or missing fact blocks.
- A single atomic reservation/check at the OMS boundary so concurrent
  candidates cannot both spend the same budget. The existing execution ledger
  deduplicates one intent; it is not a portfolio-budget reservation mechanism.

No current component qualifies the spendable-EUR and fee evidence above, and
no accepted freshness interval for those private facts is present. Therefore
this proposal **must not produce a passing runtime assessment yet**.

## Worked example

If qualified evidence later establishes a EUR 10,000 portfolio value, the
entry cap is EUR 1,000. A EUR 990 limit order plus EUR 10 bounded fees reaches
the cap; it still blocks if spendable EUR is below EUR 1,000 or another gate is
unknown. The numbers in this example are illustrative, not configured funds or
an instruction to trade.

## Authority and exclusions

CTO acceptance is needed before a normative policy or passing assessment is
implemented. The CIO's 10% limit is recorded here, but no cap is inferred from
credentials, a historical balance, or a default account size. The proposal
introduces no real credential, private call, nonce, signature, transport,
economic call, retry, Testnet, or Live capability. Kraken-only, Spot BTC/EUR,
offline and fail-closed. `REAL_ECONOMIC_CALLS = 0`.
`LIVE = LIVE_FORBIDDEN`.
