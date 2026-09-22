# ENG-TO-OPS-007 — first Testnet quote cap 6 USDT

## Authority

CTO decisions "ENG-TO-OPS-007 — First Testnet Quote Cap 6 USDT" and "QUOTE CAP VERSIONING",
implemented on base `1f0dc4863e97c81f945130827e9cd266d8929c2a` (merge of PR #21, ENG-TO-OPS-006).

## Decision

```
FIRST_ORDER_QUOTE_CAP = Decimal("6")
```

This replaces the operational first-order Testnet cap of `5 USDT` for every new
`FirstTestnetOrderAuthorization`. The earlier records `ENG-TO-OPS-004/005/006` describe `5 USDT`
and stay historical: this decision supersedes them from this change onward and they are not
rewritten.

The value is an explicit CTO value for the first Testnet order only. It is not a new global
default, not a dynamic margin and never adjusted automatically. `minNotional` (5 USDT on the
observed BTCUSDT Testnet filters) is still respected exactly: an admissible quantity `q` must
satisfy `5 <= q * price <= 6` on the exact grid, or no order is admissible.

## Single source of truth

`FIRST_ORDER_QUOTE_CAP` is defined once, in `atp.first_testnet_order.model`. Five former literal
sites now read it:

| Site | Before | After |
| --- | --- | --- |
| `QuantitySelectionEvidence.quote_cap` default | `Decimal("5")` | the constant |
| `select_quantity` quantity ceiling | `Fraction(5)` | `Fraction(constant)` |
| `select_quantity` projected notional check | `projected > 5` | `projected > constant` |
| authorization built in `prepare_check_only` | `Decimal("5")` | the constant |
| pre-watch feasibility (`feasibility.py`) | its own `Decimal("5")` | the constant |

The gate compares the projection against `auth.max_quote_notional`, so it follows the
authorization automatically. Selection, feasibility and authorization can no longer disagree.

## Versioning and identities

The policy stays `ATP_FIRST_TESTNET_ORDER_V1 / 1.0`. Schema, invariants, one-shot semantics,
environment, symbol, side, order type, `max_submissions`, Risk, quantity-selection semantics,
trust model and ledger semantics are unchanged. Only the value carried by an authorization
instance changes.

`max_quote_notional` participates in the authorization identity, so every previous authorization
at `5` and its trusted pin become incompatible with a new authorization at `6`. There is no
migration, compatibility path or pin reuse. `FirstOrderPolicy` has no cap field and its identity
is unchanged.

## What is not changed

Strategy, Risk, symbol, BUY/MARKET, sizing policy (`MAX_ADMISSIBLE_UNDER_QUOTE_CAP`), freshness
rules, filter classification, ActivationGrant, the ledger, the watcher (still read-only), the
pre-watch contracts from ENG-TO-OPS-006, `ACTIVE_ENVIRONMENTS`, and every LIVE prohibition.
`REAL_ECONOMIC_CALLS = 0`.

## Effect on feasibility (single average-price observation)

With the current single `avgPrice` observation used for both the NOTIONAL bound and the cap
projection, an admissible quantity exists exactly when the grid holds a point in
`[5 / (p * 0.00001), 6 / (p * 0.00001)]`, a window of width `100000 / p` in grid units.

- Feasible for every average price up to and including 120000 USDT.
- Not feasible for average prices in (120000, 125000), (150000, 166666.67), (200000, 250000),
  (300000, 500000) and above 600000. The pre-watch check reports NOT_FEASIBLE there; it is never
  worked around.
- The BTCUSDT Testnet `avgPrice` at run time decides; `--pre-watch-feasibility` prints it.

## Regression protection

- A guard test parses all of `src` and `scripts`, and fails on `Decimal`/`Fraction` of `5`, a
  comparison against `5`, or a cap-named binding of `5` (`quote`, `notional`, `cap`). It has its
  own positive and negative cases so unrelated five-second and five-minute constants are not
  flagged.
- A second guard fails if a hard-coded `6` appears outside `model.py`, or is defined there more
  than once.
- Consumer-equality tests cover selection, feasibility and the authorization built by the
  runtime (`READY_TO_SUBMIT` end to end, offline).
- Boundary tests prove a projection of exactly `6` is accepted and anything above it is refused
  by the gate, selection is never above the cap, minimum notional is respected, and an empty window still
  ends in `NO_ADMISSIBLE_QUANTITY` rather than a larger cap.
- Tests that hard-coded `5` were rewritten. `minNotional = 6` in the "impossible filters" case
  became `7`, because `6` is now reachable under the cap.
