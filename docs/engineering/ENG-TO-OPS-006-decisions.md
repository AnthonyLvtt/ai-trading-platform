# ENG-TO-OPS-006 — pre-watch quantity feasibility

## Authority

This implements the CTO decision "ENG-TO-OPS-006 — PRE-WATCH QUANTITY FEASIBILITY" on base
`498588c2bd736b2b17361da16aeae8645dc4a048`. The economic rules are unchanged:
`max_quote_notional = 5 USDT`, BTCUSDT, BUY MARKET, Strategy SMA 20/50 on 5m. There is no
cap increase, tolerance, margin, rounding, symbol change or `minNotional` bypass.

## Rule

For BTCUSDT BUY MARKET a quantity `q` is admissible only if, simultaneously:

- `q > 0` and `q` lies exactly on the applicable grid (`MARKET_LOT_SIZE`, or `LOT_SIZE.stepSize`
  when the MARKET step is `0`) within the applicable minimum and maximum quantity;
- `q * compatible_avg_price` satisfies every applicable NOTIONAL bound (`minNotional` when
  `applyMinToMarket`, `maxNotional` when `applyMaxToMarket`);
- `q * projected_cap_price <= 5`.

`assess_quantity_feasibility` decides existence exactly, with `Fraction`/`Decimal` only. Every
constraint is linear in `q`, so the admissible grid indices form an interval; it is empty exactly
when `ceil(max lower bound / step) > floor(min upper bound / step)`. A non-empty interval yields the
largest index as a witness, the same choice as `MAX_ADMISSIBLE_UNDER_QUOTE_CAP`. The witness is
then re-verified by the shared `check_market_quantity` (all MARKET filters except live order
capacity), so the closed form can never say FEASIBLE where the shared filter logic disagrees.

Unknown or malformed filters, a symbol not `TRADING` or without `MARKET`, stale filter evidence
(> 15 minutes), stale or future price evidence (> 10 seconds), an incompatible price source, invalid
time evidence or any failed read are **BLOCKED**, never NOT_FEASIBLE and never FEASIBLE.

## Outcomes

- `FEASIBLE`: at least one exact `q` exists at this instant.
- `NOT_FEASIBLE / NO_ADMISSIBLE_QUANTITY`: none exists. The watcher is not started.
- `BLOCKED`: evidence invalid, stale or unavailable (closed reason vocabulary).

`FEASIBLE` is a point-in-time filter to avoid consuming a grant window. It is not an admissible
future order and does not replace any final gate. If a `LONG_ENTRY` appears, price, filters,
open orders, Risk and every other proof are refreshed and revalidated as before. It authorizes
nothing: the evidence contract pins `submission_authorized` and `side_effect_performed` to
`False`.

## Composition

`check_pre_watch_feasibility` performs public unsigned GETs only: server time, `exchangeInfo` and
`avgPrice`. It uses `PublicTestnetSource`, which has no credential provider and a closed route set,
so no account, open-orders or klines route exists on it. It never evaluates Strategy, assumes a
portfolio, creates an ActivationGrant, requests a pin or reaches an economic transport.
`REAL_ECONOMIC_CALLS = 0`; `LIVE = LIVE_FORBIDDEN`. Open-order capacity (`MAX_NUM_ORDERS`) needs a
real open-orders read and is left to the existing final gate.

`scripts/submit_first_testnet_order.py` gains:

- `--pre-watch-feasibility`: standalone mode, needs no credentials, session directory, release or
  qualification; exits `0` only for `FEASIBLE`.
- `--check-only --watch` runs the same check first, before the session directory, release build,
  qualification, credentials, grant candidate or pin prompt. Anything other than `FEASIBLE`
  stops the run with that report. Non-watch `--check-only` is unchanged.

## Price evidence observation

The existing pipeline uses one `avgPrice` observation both as the compatible NOTIONAL price and
as the cap projection price (`select_quantity`, `_freshness`). The pure function takes the two
prices as separate inputs so that the CTO cases (`avg < projected`, `avg >= projected`) are
expressible and tested, and the runtime supplies the same observation for both, matching the final
gate exactly. No second price source is introduced; adding one would be a new economic rule.

Both inputs must satisfy the applicable MARKET price contract (symbol BTCUSDT, freshness,
`source_type` and `avg_price_minutes` as returned by `market_notional_price_contract`). The
projection price is not exempt: a source or window other than the authorized Exchange evidence
would open a wider capacity than the one authorized, and is BLOCKED rather than judged. This
follows the CTO review of `e665a5ed01170f76840d0f14ccfcae15e5f93c22`.

With one price, a 5 USDT cap, `minNotional = 5` and the BTCUSDT grid `0.00001`, the admissible
interval is the single point `q * p = 5`. It is non-empty only when `5 / (p * 0.00001)` is an
integer, that is when the average price is exactly `500000 / k` for an integer `k`. The check
therefore reports NOT_FEASIBLE for almost every real price. This is the honest current rule; it is
reported, not worked around.

## Documentation drift

The README described a foundation-only repository and is updated to the real state. The Accepted
`SPEC-*` documents cited by the ADRs and engineering records are not present in this repository.
They are neither reconstructed nor invented here; their reintegration is a separate documentation
task.

## Regression boundaries

No change to Strategy, Risk, Accounting, OMS, Observability EventTypes, ActivationGrant,
FirstTestnetOrderAuthorization, the ledger or `ACTIVE_ENVIRONMENTS`. `check_market_filters` keeps
its behaviour: it now delegates to a shared implementation with capacity enforcement on, and the
new `check_market_quantity` is that implementation with capacity off. `TestnetReadOnlySource` keeps
its surface; only the GET helper is shared.
