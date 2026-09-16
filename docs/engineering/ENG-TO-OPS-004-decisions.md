# ENG-TO-OPS-004 — Binance filter applicability and open-order freshness

Authority: CTO decisions ENG-TO-OPS-004, including OPEN-ORDERS FRESHNESS.
Base: 112864cfe9065c1ad124e5c6000afe514154bddd.

The supported shape is the existing standalone, unpriced Spot BUY MARKET,
without iceberg, trailing, order-list or amendment fields. Classification is
recorded in deterministic FilterApplicabilityEvidence and saved by preparation.
Unknown filters remain blocked. Known filters must still satisfy their schema.

| Filter | Classification for this shape |
| --- | --- |
| PRICE_FILTER | Parsed, not applicable |
| LOT_SIZE | Quantity grid fallback when MARKET step is disabled |
| MARKET_LOT_SIZE | Enabled bounds and enabled step |
| MIN_NOTIONAL / NOTIONAL | Applicable MARKET flags; NOTIONAL takes priority |
| ICEBERG_PARTS | Not applicable without icebergQty |
| TRAILING_DELTA | Not applicable |
| PERCENT_PRICE_BY_SIDE | Not applicable without a price |
| MAX_NUM_ORDERS | Fresh symbol-scoped open orders plus one must fit limit |
| MAX_NUM_ORDER_LISTS | Not applicable to standalone order |
| MAX_NUM_ALGO_ORDERS | Not applicable to plain MARKET |
| MAX_NUM_ORDER_AMENDS | Not applicable to new creation |

A zero MARKET_LOT_SIZE step disables its grid; it is not itself a valid grid.
LOT_SIZE supplies the positive exact step. Both sets of enabled bounds are
respected in this fallback. Existing positive MARKET step semantics are retained.
No quantity is silently adjusted and all applicable constraints are rechecked.
The quote cap remains exactly 5 USDT. With an applicable minimum of 5, selection
requires exact equality using the compatible Exchange average price. A missing
exact grid point yields NO_ADMISSIBLE_QUANTITY. No float, cap increase or order.

OpenOrdersEvidence is immutable, TESTNET, symbol-scoped and identity-bound to a
successful complete read-only response, timestamped immediately after receipt.
Duplicate IDs, unknown status, partial envelopes and failed reads block. No empty
fallback exists. MAX_OPEN_ORDERS_EVIDENCE_AGE is 10 seconds; exactly 10 is valid,
older evidence yields OPEN_ORDERS_EVIDENCE_STALE. Missing, forged, future-dated or
mismatched evidence is invalid. Capacity is checked during selection and twice
at the first-order gate, including its final fresh clock sample after both pins.

After the second pin, stale capacity evidence can be refreshed once with the
existing GET-only source. Grant, first-order authorization, pins and Risk are
unchanged. If the refreshed order payload differs from the portfolio read used
by Risk, preparation blocks with PORTFOLIO_STATE_UNKNOWN. A failed refresh
blocks. The remaining time, price, filter and authorization deadlines still
apply: refreshing order evidence does not refresh prices or reauthorize anything.

Tests use the operator-provided public Testnet metadata as an offline fixture.
They cover every listed filter, malformed/unknown filters, exact/impossible
5-USDT selection, capacity limits, freshness boundaries, post-pin refresh failure,
changed portfolio, unchanged authorization identities and zero economic calls.
No real check-only, secret injection, economic network, merge or Live activation
is part of this implementation.
