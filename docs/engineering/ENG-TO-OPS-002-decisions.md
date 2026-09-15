# ENG-TO-OPS-002 — Runtime composition for check-only

Authority: CTO mission and complementary Strategy/portfolio/quantity decisions.
Implementation base: `ebe53c1c75bec9a4d1518e65b0e5709a08d7bd7f`.

## Fixed scope

Only Binance Spot TESTNET BTCUSDT BUY MARKET preparation. The explicit procedure
configuration is SMA crossover 20/50, 5m, 51 final candles, projected quote cap
`Decimal("5")`. These are procedure parameters, not application defaults.
`ACTIVE_ENVIRONMENTS` is unchanged. TESTNET Strategy construction and evaluation
require a currently valid process-sealed RuntimeAuthorizationContext. LIVE is
always forbidden. Strategy provenance includes that context and interval.

No signal is fabricated. Actual NO_ACTION or EXIT returns FIRST_ORDER_NOT_READY.
Risk evaluates the actual Strategy evaluation against the current account projection.
No old Risk decision is loaded, no Accounting state is manufactured or mutated.

## Source and trust composition

The command rebuilds the exact clean main checkout through the canonical release
producer, including uv lock check, validation, ATP qualification, wheel validation
and manifest. It then runs the offline TQ collector against the same source. Typed
proofs pass directly from those producers to the existing activation inspectors;
there is no pickle or caller-supplied green-validation boolean.

The operator explicitly confirms the previously verified permissions for the
dedicated Testnet key. The provider captures the environment pair once with a new
opaque reference. Environment changes cannot replace the material behind that
reference. The command removes the two variables from its own process environment
before running build/test children; the snapshot retains material in memory only.
No permission is inferred from authentication or account balances.

An exact local pin attests the manual permission artifact (maximum 24h). Check-only
session authorities pin the newly constructed activation grant and first-order
authorization. They are never default authorities. Grant validity is 30 minutes;
first-order validity is 15 minutes starting at preparation. The existing gate also
requires all context/credential windows to remain valid.

Session files are created outside the checkout in a new private directory. They
contain no secret, and are excluded from release/TQ output. Serialized files do
not recreate process-local trust seals. Re-running creates a new session, never a
silent ledger reset. No grant or credential is committed to the repository.

## Read-only sources

The sole HTTP method is GET at the closed host `testnet.binance.vision`:
`/api/v3/time`, `exchangeInfo`, `klines`, `trades`, `avgPrice`, `account`, `openOrders`.
Only account/openOrders require HMAC. No redirects, endpoint override or retry.
No order, cancel, permission administration, withdrawal or Live endpoint exists.
Protocol references: Binance official Spot Testnet REST API, market-data endpoints
and account endpoints (`https://developers.binance.com/docs/binance-spot-api-docs/testnet/rest-api`).

Klines are requested through the last closed 5m boundary. Parsing enforces 51
contiguous final ordered causal candles, numeric validity and OHLC consistency.
Account BTC/USDT balances and BTCUSDT open orders are read in the same check.
Missing/malformed data blocks. Any BTC balance or open order is conservatively
treated as existing exposure; Risk's existing max_positions=1 gate remains decisive.
No ACK is interpreted as a fill, and no balance is booked into Accounting.

## Exact quantity and time

Selection computes a finite integer grid index using Fraction/integer arithmetic,
then exact Decimal multiplication. It selects the largest quantity satisfying the
5 USDT projection, applicable maxQty and applicable maximum notional, and then
revalidates all filters, including minima. No existing quantity is silently rounded.
The deterministic selection evidence records price/filter identities and grid facts.
No admissible grid point yields NO_ADMISSIBLE_QUANTITY; the cap never increases.
An absent/zero step that establishes no finite grid blocks. Unknown filters block.

The applicable average/last-price contract is enforced. Without applicable Binance
notional rules, LAST_PRICE is still required for the ATP cap. Average price requires
the exact window; a recent trade supplies last price and exchange timestamp.
The final existing gate rechecks time/skew <=5s, price age <=10s and filters <=15m.
This is a pre-submission projected cap, not an exchange-side guaranteed spend.

## Check-only and review

There is no economic transport in this composition. Its submit method always
refuses and the orchestrator calls run_first_order with execute=False. The command
rejects --execute before credentials, authorities or network access. Check-only does
not reserve ATTEMPT_STARTED. A READY result reports NOT_ATTEMPTED and zero economic
calls. It is not permission to send an order. There is no waiting loop for a BUY.

After CTO review and approved merge only, run from a clean main checkout. Choose
an explicit human release version and a new absolute session directory outside Git.
In Bash, enter secrets without shell-history literals or terminal echo:

```bash
read -r -s -p 'Testnet API key: ' ATP_BINANCE_TESTNET_API_KEY
read -r -s -p 'Testnet API secret: ' ATP_BINANCE_TESTNET_API_SECRET
export ATP_BINANCE_TESTNET_API_KEY ATP_BINANCE_TESTNET_API_SECRET
uv run --frozen python scripts/submit_first_testnet_order.py \
  --check-only \
  --source-commit "$(git rev-parse HEAD)" \
  --release-version internal-check-001 \
  --session-dir /absolute/private/new-atp-check-session \
  --confirm-testnet-permissions
unset ATP_BINANCE_TESTNET_API_KEY ATP_BINANCE_TESTNET_API_SECRET
```

The confirmation is permitted only after manual Testnet ownership, Spot trading,
and absent withdrawal verification for this exact captured key. Never paste keys
into chat. No network check-only or real credential is used during implementation.
READY_TO_SUBMIT is conditional; a genuine no-signal, exposure, stale evidence or
infeasible 5 USDT filter combination legitimately blocks. Stop for CTO review of
the resulting non-secret evidence. First economic execution remains a separate task.
