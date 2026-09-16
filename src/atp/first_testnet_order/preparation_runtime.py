"""Explicit check-only composition. Its transport cannot dispatch an order."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from atp.exchange.filters import (
    MAX_OPEN_ORDERS_EVIDENCE_AGE,
    NotionalPriceEvidence,
    market_filter_applicability,
    market_notional_price_contract,
    parse_open_orders,
    parse_symbol_filters,
)
from atp.exchange.model import ExchangeOrderRequest, Side, UpstreamOrderProof, canonical
from atp.exchange.read_only import EvidenceError, decimal_field, encoded, timestamp
from atp.exchange.time_evidence import parse_server_time_evidence
from atp.exchange.transport import TransportReply
from atp.first_testnet_order.execution import (
    TESTNET_ENDPOINT,
    FirstOrderTransport,
    SubmissionPermit,
    run_first_order,
)
from atp.first_testnet_order.gate import FirstOrderInputs, GateClock, GateTimeSample
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.model import (
    FirstOrderPolicy,
    FirstTestnetOrderAuthorization,
    TrustedFirstOrderAuthorizationEvidence,
)
from atp.first_testnet_order.preparation import (
    portfolio_evidence,
    portfolio_for_risk,
    select_quantity,
)
from atp.first_testnet_order.preparation_data import candle_snapshot
from atp.first_testnet_order.trust import TrustedFirstOrderAuthority, trust_first_order
from atp.ops import observability_evidence, readiness
from atp.ops.model import QualificationReference
from atp.release_deployment import promote
from atp.release_deployment.model import ReleaseBundle, Target
from atp.risk import (
    DeterministicRiskEngine,
    InstrumentClass,
    MarketType,
    PositionDirection,
    RiskEvaluationContext,
    RiskMarketContext,
    RiskPolicy,
    RiskPolicyId,
    RiskStatus,
)
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from atp.shared.time import LogicalTime
from atp.strategy import (
    SignalKind,
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluationContext,
    StrategyId,
)
from atp.testnet_activation.composition import (
    CredentialCapabilityAuthority,
    TrustedActivationAuthority,
    validate_activation,
)
from atp.testnet_activation.contracts import (
    ActivationPolicy,
    TestnetActivationGrant,
    TrustedActivationGrantEvidence,
)
from atp.testnet_qualification.model import (
    TestnetCapabilityEvidence,
    TestnetQualificationSuiteResult,
)


class ReadOnlySource(Protocol):
    def read(self, resource: str, parameters: tuple[tuple[str, str], ...] = ()) -> object: ...


class ReadOnlyClock(GateClock):
    def __init__(self, source: ReadOnlySource, now: Callable[[], datetime]) -> None:
        self.source, self.now = source, now

    def read(self) -> GateTimeSample:
        raw = self.source.read("time")
        at = self.now()
        evidence = parse_server_time_evidence(raw, at)
        if evidence is None or abs(evidence.server_time - at) > timedelta(seconds=5):
            raise EvidenceError("TIME_EVIDENCE_INVALID")
        return GateTimeSample(at, evidence)


class CheckOnlyTransport(FirstOrderTransport):
    def __init__(self, source: ContentIdentity) -> None:
        self.source = source

    @property
    def endpoint(self) -> str:
        return TESTNET_ENDPOINT

    @property
    def credential_source_identity(self) -> ContentIdentity:
        return self.source

    def submit(
        self, permit: SubmissionPermit, order: ExchangeOrderRequest, at: datetime, readiness: object
    ) -> TransportReply:
        raise EvidenceError("CHECK_ONLY_COMPOSITION")


class _SessionGrantAuthority(TrustedActivationAuthority):
    def __init__(self, external_pin: ContentIdentity) -> None:
        self.pin = external_pin

    @property
    def accepted_grant_identity(self) -> ContentIdentity:
        return self.pin

    def attest(self, grant: TestnetActivationGrant) -> TrustedActivationGrantEvidence:
        return TrustedActivationGrantEvidence(
            grant.content_identity,
            ActivationPolicy().content_identity,
            grant.source_commit_sha,
            grant.repository_identity,
            grant.release_candidate_identity,
            grant.testnet_qualification_identity,
            "ENG-TO-OPS-002-check-only",
        )


class _SessionFirstAuthority(TrustedFirstOrderAuthority):
    def __init__(self, external_pin: ContentIdentity) -> None:
        self.pin = external_pin

    @property
    def accepted_authorization_identity(self) -> ContentIdentity:
        return self.pin

    def attest(
        self, authorization: FirstTestnetOrderAuthorization
    ) -> TrustedFirstOrderAuthorizationEvidence:
        return TrustedFirstOrderAuthorizationEvidence(
            authorization.content_identity,
            FirstOrderPolicy().content_identity,
            "ENG-TO-OPS-002-check-only",
        )


def prepare_check_only(
    *,
    source: ReadOnlySource,
    now: Callable[[], datetime],
    release: ReleaseBundle,
    wheel: bytes,
    tq: TestnetQualificationSuiteResult,
    tq_evidence: TestnetCapabilityEvidence,
    credential_source_identity: ContentIdentity,
    credential_authority: CredentialCapabilityAuthority,
    workspace: Path,
    ledger: TestnetSubmissionLedger,
    artifact_sink: Callable[[str, object], None] | None = None,
    trust_pin_source: Callable[[str], ContentIdentity | None] | None = None,
) -> dict[str, object]:
    """No execute option. Every proof belongs to this evaluation, never a cached Risk result."""
    # Establish capabilities before any network read, independently of account responses.
    credential_authority.attest(credential_source_identity)
    clock = ReadOnlyClock(source, now)
    at = clock.read().gate_evaluation_time
    grant = TestnetActivationGrant(
        release.source.source_commit_sha,
        release.source.repository_identity,
        release.candidate.content_identity,
        release.manifest.content_identity,
        tq.content_identity,
        ("BTCUSDT",),
        ("MARKET",),
        at,
        at + timedelta(minutes=30),
        "CTO",
    )

    def external_pin(
        name: str, artifact: object, identity: ContentIdentity
    ) -> ContentIdentity | None:
        if artifact_sink is not None:
            artifact_sink(name, artifact)
        # The authority channel receives only the kind, never the candidate or its identity.
        pin = None if trust_pin_source is None else trust_pin_source(name)
        return pin if type(pin) is ContentIdentity and pin == identity else None

    grant_pin = external_pin("activation-grant", grant, grant.content_identity)
    if grant_pin is None:
        return {
            "status": "BLOCKED",
            "reason_code": "TRUST_PIN_REQUIRED",
            "activation_grant_identity": str(grant.content_identity),
            "real_economic_calls": 0,
            "LIVE": "LIVE_FORBIDDEN",
        }
    at = clock.read().gate_evaluation_time
    activation = validate_activation(
        grant,
        source_commit_sha=release.source.source_commit_sha,
        repository_identity=release.source.repository_identity,
        release=release,
        wheel=wheel,
        tq=tq,
        tq_evidence=tq_evidence,
        credential_source_identity=credential_source_identity,
        symbol="BTCUSDT",
        order_type="MARKET",
        at=at,
        grant_authority=_SessionGrantAuthority(grant_pin),
        credential_authority=credential_authority,
    )
    context = activation.context
    if context is None:
        raise EvidenceError(activation.reason_code.value)
    metadata = source.read("exchangeInfo", (("symbol", "BTCUSDT"),))
    if (
        type(metadata) is not dict
        or type(metadata.get("symbols")) is not list
        or len(metadata["symbols"]) != 1
    ):
        raise EvidenceError("INVALID_FILTER_EVIDENCE")
    symbol = metadata["symbols"][0]
    if (
        type(symbol) is not dict
        or symbol.get("baseAsset") != "BTC"
        or symbol.get("quoteAsset") != "USDT"
        or symbol.get("isSpotTradingAllowed") is not True
    ):
        raise EvidenceError("INVALID_FILTER_EVIDENCE")
    filters = parse_symbol_filters(symbol, now())
    if filters is None or market_notional_price_contract(filters) is None:
        raise EvidenceError("INVALID_FILTER_EVIDENCE")
    applicability = market_filter_applicability(filters)
    if artifact_sink is not None:
        artifact_sink("filter-applicability", applicability)
    server = clock.read().evidence.server_time
    elapsed = server - datetime(1970, 1, 1, tzinfo=UTC)
    millis = elapsed.days * 86400000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
    raw = source.read(
        "klines",
        (
            ("symbol", "BTCUSDT"),
            ("interval", "5m"),
            ("limit", "51"),
            ("endTime", str(millis // 300000 * 300000 - 1)),
        ),
    )
    account = source.read("account")
    orders = source.read("openOrders", (("symbol", "BTCUSDT"),))
    open_orders = parse_open_orders(orders, "BTCUSDT", now(), complete=True)
    at = clock.read().gate_evaluation_time
    portfolio = portfolio_evidence(account, orders, at)
    snapshot, universe = candle_snapshot(raw, at)
    strategy = SmaCrossoverStrategy(
        StrategyId("sma-crossover"), "1.0.0", SmaCrossoverConfig(short_window=20, long_window=50)
    ).evaluate(
        StrategyEvaluationContext(
            Environment.TESTNET, snapshot, universe, LogicalTime(at), "BTCUSDT", context, "5m"
        )
    )
    report: dict[str, object] = {
        "source_commit_sha": release.source.source_commit_sha,
        "status": "BLOCKED",
        "reason_code": "FIRST_ORDER_NOT_READY",
        "real_economic_calls": 0,
        "LIVE": "LIVE_FORBIDDEN",
        "strategy_identity": str(strategy.content_identity),
        "strategy_signal": None if strategy.signal is None else strategy.signal.kind.value,
        "portfolio_evidence_identity": str(portfolio.content_identity),
    }
    if strategy.signal is None or strategy.signal.kind is not SignalKind.LONG_ENTRY:
        return report
    risk = DeterministicRiskEngine(
        RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    ).evaluate(
        RiskEvaluationContext(
            strategy,
            RiskMarketContext(
                "BTCUSDT",
                MarketType.SPOT,
                PositionDirection.LONG,
                False,
                Decimal(1),
                InstrumentClass.SPOT,
                "TESTNET",
            ),
            portfolio_for_risk(portfolio),
            context,
        )
    )
    report["risk_status"] = risk.status.value
    if risk.status is not RiskStatus.APPROVED or risk.decision is None:
        return report
    contract = market_notional_price_contract(filters)
    assert contract is not None
    kind, window = contract
    if kind == "EXCHANGE_AVERAGE_PRICE":
        value = source.read("avgPrice", (("symbol", "BTCUSDT"),))
        if type(value) is not dict or type(value.get("mins")) is not int or value["mins"] != window:
            raise EvidenceError("PRICE_EVIDENCE_STALE")
        amount, effective = decimal_field(value.get("price")), timestamp(value.get("closeTime"))
    else:
        value = source.read("trades", (("symbol", "BTCUSDT"), ("limit", "1")))
        if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
            raise EvidenceError("PRICE_EVIDENCE_STALE")
        amount, effective = decimal_field(value[0].get("price")), timestamp(value[0].get("time"))
    price = NotionalPriceEvidence(
        "BTCUSDT", amount, kind, ContentIdentity.from_canonical(value), now(), effective, window
    )
    at = clock.read().gate_evaluation_time
    selection = select_quantity(filters, price, at, open_orders)
    if portfolio.usdt_free < selection.projected_quote_notional:
        raise EvidenceError("FIRST_ORDER_NOT_READY")
    values = dict(
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=selection.selected_quantity,
        risk_decision_id=str(risk.decision.risk_decision_id),
        risk_decision_identity=risk.decision.content_identity,
        strategy_evaluation_identity=strategy.content_identity,
        environment="TESTNET",
        created_at=at,
    )
    proof = UpstreamOrderProof(
        **values,  # type: ignore[arg-type]
        content_identity=ContentIdentity.from_canonical(
            {k: canonical(v) for k, v in values.items()}
        ),
    )
    q = release.qualification.result
    ops = readiness(
        dict(
            environment="TESTNET",
            app_name="ATP",
            config_schema_version="1.0",
            workspace_path=str(workspace),
            artifacts_path=str(workspace / "artifacts"),
            observability_enabled=True,
            deterministic_mode=True,
            qualification_required=True,
            ops_policy_id="ATP_OPS_V1",
            ops_policy_version="1.0",
        ),
        qualification=QualificationReference(q, q.content_identity, q.qualification_run_id),
        observability=observability_evidence(),
        runtime_authorization=context,
        at=at,
    )
    promotion = promote(release, wheel, Target.TESTNET, runtime_authorization=context, at=at)
    authorization = FirstTestnetOrderAuthorization(
        grant.content_identity,
        context.content_identity,
        release.source.source_commit_sha,
        release.candidate.content_identity,
        tq.content_identity,
        proof.content_identity,
        ledger.content_identity,
        "BTCUSDT",
        selection.selected_quantity,
        Decimal("5"),
        at,
        at + timedelta(minutes=15),
    )
    first_pin = external_pin(
        "first-order-authorization", authorization, authorization.content_identity
    )
    if first_pin is None:
        report.update(
            reason_code="TRUST_PIN_REQUIRED",
            activation_grant_identity=str(grant.content_identity),
            first_order_identity=str(authorization.content_identity),
        )
        return report
    receipt = trust_first_order(authorization, _SessionFirstAuthority(first_pin))
    # Read-only refresh may replace stale capacity facts, never grants, pins or Risk.
    if now() - open_orders.observed_at > MAX_OPEN_ORDERS_EVIDENCE_AGE:
        refreshed = source.read("openOrders", (("symbol", "BTCUSDT"),))
        open_orders = parse_open_orders(refreshed, "BTCUSDT", now(), complete=True)
        if open_orders.source_identity != portfolio.orders_identity:
            raise EvidenceError("PORTFOLIO_STATE_UNKNOWN")
    result = run_first_order(
        FirstOrderInputs(
            authorization,
            receipt,
            proof,
            risk.decision,
            strategy,
            grant,
            context,
            ops,
            release,
            wheel,
            promotion,
            filters,
            price,
            open_orders,
        ),
        clock=clock,
        ledger=ledger,
        transport=CheckOnlyTransport(credential_source_identity),
        execute=False,
    )
    if artifact_sink is not None:
        for name, artifact in (
            ("quantity-selection", selection),
            ("open-orders-evidence", open_orders),
            ("portfolio-evidence", portfolio),
            ("upstream-proof", proof),
            ("strategy-evaluation", strategy),
        ):
            artifact_sink(name, artifact)
    report.update(
        status=result.status,
        reason_code=result.reason_code.value,
        quantity_selection=encoded(selection),
        filter_applicability=encoded(applicability),
        open_orders_evidence_identity=str(open_orders.content_identity),
        quantity_selection_identity=str(selection.content_identity),
        upstream_proof_identity=str(proof.content_identity),
        risk_identity=str(risk.decision.content_identity),
        activation_grant_identity=str(grant.content_identity),
        first_order_identity=str(authorization.content_identity),
        client_order_id=authorization.client_order_id,
        ledger_state=result.state.value,
        ops=ops.readiness_status.value,
        promotion=promotion.status.value,
    )
    return report
