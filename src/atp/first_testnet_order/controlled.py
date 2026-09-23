"""Explicit operator composition; refreshed evidence never changes approved order facts."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from atp.exchange.filters import (
    NotionalPriceEvidence,
    OpenOrdersEvidence,
    SymbolFilterEvidence,
    market_notional_price_contract,
    parse_open_orders,
    parse_symbol_filters,
)
from atp.exchange.model import ExchangeOrderRequest
from atp.exchange.read_only import EvidenceError, decimal_field, timestamp, verify_record
from atp.first_testnet_order.execution import SubmissionPermit, run_first_order
from atp.first_testnet_order.gate import FirstOrderInputs, GateClock, evaluate_first_order
from atp.first_testnet_order.ledger import LedgerUnavailable, TestnetSubmissionLedger
from atp.first_testnet_order.model import (
    FIRST_ORDER_QUOTE_CAP,
    FirstOrderResult,
    FirstTestnetOrderAuthorization,
    Reason,
)
from atp.first_testnet_order.preparation import (
    TestnetPortfolioEvidence,
    portfolio_evidence,
    portfolio_for_risk,
)
from atp.release_deployment.model import ReleaseBundle
from atp.release_deployment.source import inspect_source
from atp.risk import DeterministicRiskEngine, RiskEvaluationContext, RiskPolicy, RiskStatus
from atp.risk.model import RiskDecision
from atp.shared.identity import ContentIdentity
from atp.strategy import StrategyEvaluation
from atp.testnet_activation.contracts import RuntimeAuthorizationContext

# One host/user campaign, independent of release checkout and temporary session directories.
# Provisioning/adoption is an explicit operational action, never performed by execution.
OPERATIONAL_LEDGER_PATH = Path(
    "/Users/anthonylvtt/Library/Application Support/ATP/first-testnet-order/submission.sqlite"
)
MAX_ACCOUNT_AGE = timedelta(seconds=10)


class ReadSource(Protocol):
    def read(self, resource: str, parameters: tuple[tuple[str, str], ...] = ()) -> object: ...


def operational_ledger() -> TestnetSubmissionLedger:
    path = OPERATIONAL_LEDGER_PATH
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise LedgerUnavailable("Canonical campaign ledger required")
    ledger = TestnetSubmissionLedger(path)
    ledger.inspect_campaign()
    return ledger


class FinalBoundary:
    """Trusted transport dependency. Re-read after connection, then check deadlines at write."""

    def __init__(
        self,
        source: ReadSource,
        clock: GateClock,
        now: Callable[[], datetime],
        inputs: FirstOrderInputs,
        portfolio: TestnetPortfolioEvidence,
        ledger: TestnetSubmissionLedger,
        source_root: Path,
    ) -> None:
        self.source, self.clock, self.now = source, clock, now
        self.inputs, self.portfolio, self.ledger = inputs, portfolio, ledger
        self.source_root = source_root
        self.refreshed: FirstOrderInputs | None = None
        self.account_at: datetime | None = None
        self.permit_identity: ContentIdentity | None = None

    def refresh(self) -> FirstOrderInputs:
        auth, risk, strategy, ctx = (
            self.inputs.authorization,
            self.inputs.risk,
            self.inputs.strategy,
            self.inputs.runtime_authorization,
        )
        if not verify_record(auth, FirstTestnetOrderAuthorization):
            raise EvidenceError("FIRST_ORDER_AUTHORIZATION_INVALID")
        assert isinstance(auth, FirstTestnetOrderAuthorization)
        if (auth.symbol, auth.environment, auth.side, auth.order_type, auth.max_quote_notional) != (
            "BTCUSDT",
            "TESTNET",
            "BUY",
            "MARKET",
            FIRST_ORDER_QUOTE_CAP,
        ):
            raise EvidenceError("FIRST_ORDER_AUTHORIZATION_MISMATCH")
        if (
            not isinstance(risk, RiskDecision)
            or not isinstance(strategy, StrategyEvaluation)
            or not isinstance(ctx, RuntimeAuthorizationContext)
            or not verify_record(self.portfolio, TestnetPortfolioEvidence)
            or risk.provenance.portfolio_state_identity
            != portfolio_for_risk(self.portfolio).content_identity
        ):
            raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
        self.clock.read()
        filter_at = self.now()
        raw = self.source.read("exchangeInfo", (("symbol", "BTCUSDT"),))
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("symbols"), list)
            or len(raw["symbols"]) != 1
        ):
            raise EvidenceError("INVALID_FILTER_EVIDENCE")
        item = raw["symbols"][0]
        if (
            not isinstance(item, dict)
            or (
                item.get("baseAsset"),
                item.get("quoteAsset"),
                item.get("isSpotTradingAllowed"),
            )
            != ("BTC", "USDT", True)
            or item.get("isSpotTradingAllowed") is not True
        ):
            raise EvidenceError("INVALID_FILTER_EVIDENCE")
        filters = parse_symbol_filters(item, filter_at)
        contract = market_notional_price_contract(filters)
        if filters is None or contract is None:
            raise EvidenceError("INVALID_FILTER_EVIDENCE")
        account_at = self.now()
        account = self.source.read("account")
        orders_at = self.now()
        raw_orders = self.source.read("openOrders", (("symbol", "BTCUSDT"),))
        orders = parse_open_orders(raw_orders, "BTCUSDT", orders_at, complete=True)
        portfolio = portfolio_evidence(account, raw_orders, account_at)
        kind, window = contract
        if kind == "EXCHANGE_AVERAGE_PRICE":
            value = self.source.read("avgPrice", (("symbol", "BTCUSDT"),))
            if (
                type(value) is not dict
                or type(value.get("mins")) is not int
                or value["mins"] != window
            ):
                raise EvidenceError("PRICE_EVIDENCE_STALE")
            amount, effective = decimal_field(value.get("price")), timestamp(value.get("closeTime"))
        else:
            value = self.source.read("trades", (("symbol", "BTCUSDT"), ("limit", "1")))
            if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
                raise EvidenceError("PRICE_EVIDENCE_STALE")
            amount, effective = (
                decimal_field(value[0].get("price")),
                timestamp(value[0].get("time")),
            )
        price = NotionalPriceEvidence(
            "BTCUSDT",
            amount,
            kind,
            ContentIdentity.from_canonical(value),
            self.now(),
            effective,
            window,
        )
        approved = DeterministicRiskEngine(
            RiskPolicy.v1(
                policy_id=risk.provenance.risk_policy_id,
                version=risk.provenance.risk_policy_version,
            )
        ).evaluate(
            RiskEvaluationContext(
                strategy, risk.provenance.market_context, portfolio_for_risk(portfolio), ctx
            )
        )
        if approved.status is not RiskStatus.APPROVED:
            raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
        # Any changed balances/exposure invalidate this candidate; never rebuild its proof/pin.
        names = (
            "btc_free",
            "btc_locked",
            "usdt_free",
            "usdt_locked",
            "open_order_count",
            "orders_identity",
            "state",
        )
        if any(getattr(portfolio, name) != getattr(self.portfolio, name) for name in names):
            raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
        if Fraction(portfolio.usdt_free) < Fraction(auth.quantity) * Fraction(price.price):
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        refreshed = replace(self.inputs, filters=filters, price=price, open_orders=orders)
        result, order, _ = evaluate_first_order(refreshed, self.clock)
        if order is None:
            raise EvidenceError(result.reason_code.value)
        self.refreshed, self.account_at = refreshed, account_at
        if self.current_time() is None:
            raise EvidenceError("FIRST_ORDER_NOT_READY")
        return refreshed

    def current_time(self) -> datetime | None:
        if self.refreshed is None or self.account_at is None:
            return None
        inputs = self.refreshed
        orders, price, filters, auth = (
            inputs.open_orders,
            inputs.price,
            inputs.filters,
            inputs.authorization,
        )
        if not (
            isinstance(orders, OpenOrdersEvidence)
            and isinstance(price, NotionalPriceEvidence)
            and isinstance(filters, SymbolFilterEvidence)
            and isinstance(auth, FirstTestnetOrderAuthorization)
        ):
            return None
        # Includes trusted server time, price/filter freshness, authorization and context expiry.
        result, order, at = evaluate_first_order(inputs, self.clock)
        if order is None or at is None or orders.complete is not True:
            return None
        if not (
            self.account_at <= at <= self.account_at + MAX_ACCOUNT_AGE
            and orders.observed_at <= at <= orders.observed_at + timedelta(seconds=10)
        ):
            return None
        return at

    def before_post(self, permit: SubmissionPermit, order: ExchangeOrderRequest) -> datetime | None:
        try:
            auth = self.inputs.authorization
            release = self.inputs.release
            if (
                not isinstance(auth, FirstTestnetOrderAuthorization)
                or not isinstance(release, ReleaseBundle)
                or not self.source_root.is_absolute()
                or inspect_source(self.source_root) != release.source
            ):
                return None
            canonical = operational_ledger()
            if (
                canonical.content_identity != self.ledger.content_identity
                or auth.submission_ledger_identity != canonical.content_identity
            ):
                return None
            canonical.reservation_identity(auth.content_identity, auth.client_order_id)
            if (
                permit.authorization_identity != auth.content_identity
                or permit.request_identity != order.content_identity
            ):
                return None
            if self.permit_identity is None:
                refreshed = self.refresh()
                _, expected, _ = evaluate_first_order(refreshed, self.clock)
                if expected != order:
                    return None
                self.permit_identity = permit.content_identity
            if self.permit_identity != permit.content_identity:
                return None
            return self.current_time()
        except (ValueError, OSError):
            return None


def execute_controlled(
    inputs: FirstOrderInputs,
    portfolio: TestnetPortfolioEvidence,
    *,
    source: ReadSource,
    clock: GateClock,
    now: Callable[[], datetime],
    ledger: TestnetSubmissionLedger,
    credentials: object,
    source_root: Path,
) -> FirstOrderResult:
    from atp.exchange.first_order_transport import FirstOrderBinanceTestnetTransport
    from atp.testnet_activation.runtime_credentials import ReferencedEnvironmentCredentialsProvider

    try:
        canonical = operational_ledger()
        if canonical.content_identity != ledger.content_identity:
            return FirstOrderResult(Reason.SUBMISSION_LEDGER_UNAVAILABLE)
        if canonical.inspect_campaign():
            return FirstOrderResult(Reason.FIRST_ORDER_ALREADY_CONSUMED)
        if not isinstance(credentials, ReferencedEnvironmentCredentialsProvider):
            return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY)
        ctx = inputs.runtime_authorization
        if (
            not isinstance(ctx, RuntimeAuthorizationContext)
            or credentials.reference.content_identity != ctx.credential_source_identity
        ):
            return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY)
        boundary = FinalBoundary(source, clock, now, inputs, portfolio, ledger, source_root)
        fresh = boundary.refresh()
        transport = FirstOrderBinanceTestnetTransport(
            credentials, ctx.credential_source_identity, clock, boundary
        )
        return run_first_order(fresh, clock=clock, ledger=ledger, transport=transport, execute=True)
    except (ValueError, OSError):
        return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY)


def reconcile_controlled(
    authorization: object, *, credentials: object, now: Callable[[], datetime]
) -> tuple[FirstOrderResult, object]:
    """Explicit read-only recovery, also after restart/expiry; never submits or renews trust."""
    from atp.first_testnet_order.model import SubmissionState
    from atp.first_testnet_order.preparation_http import ReconciliationReadOnlySource
    from atp.first_testnet_order.reconciliation import reconcile_first_order
    from atp.testnet_activation.runtime_credentials import ReferencedEnvironmentCredentialsProvider

    unknown = FirstOrderResult(Reason.SUBMISSION_STATE_UNKNOWN, SubmissionState.UNKNOWN)
    if not verify_record(authorization, FirstTestnetOrderAuthorization) or not isinstance(
        credentials, ReferencedEnvironmentCredentialsProvider
    ):
        return unknown, None
    assert isinstance(authorization, FirstTestnetOrderAuthorization)
    auth = authorization
    try:
        ledger = operational_ledger()
        if ledger.content_identity != auth.submission_ledger_identity:
            return unknown, None
        ledger.reservation_identity(auth.content_identity, auth.client_order_id)
        source = ReconciliationReadOnlySource(credentials)
        order = source.read(
            "order", (("symbol", auth.symbol), ("origClientOrderId", auth.client_order_id))
        )
        if not isinstance(order, dict) or type(order.get("orderId")) is not int:
            return unknown, None
        trades = source.read(
            "myTrades", (("symbol", auth.symbol), ("orderId", str(order["orderId"])))
        )
        # One bounded read. Incomplete fills fail closed; no automatic polling/pagination retry.
        return reconcile_first_order(auth, ledger, order, trades, now())
    except (ValueError, OSError):
        return unknown, None
