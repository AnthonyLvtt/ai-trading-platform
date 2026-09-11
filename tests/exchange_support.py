"""CTO-authorized fake-only TEST -> TESTNET fixture; never imported by runtime."""

from dataclasses import replace
from decimal import Decimal

from atp.exchange.adapter import ExchangeAdapter, _order_error
from atp.exchange.model import (
    ExchangePolicy,
    Reason,
    Side,
    TestnetExecutionAuthorization,
    UpstreamOrderProof,
    canonical,
    request_from_proof,
    valid,
)
from atp.exchange.transport import Operation, TransportReply, wire_parameters
from atp.risk import DeterministicRiskEngine, RiskEvaluationContext, RiskStatus
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from atp.strategy import SignalKind, StrategyEvaluation
from tests.unit.test_risk_engine import (
    NOW,
    empty_portfolio,
    market_context,
    open_portfolio,
    open_position,
    policy,
    strategy_evaluation,
)


def record(cls, **data):
    return cls(
        **data,
        content_identity=ContentIdentity.from_canonical({k: canonical(v) for k, v in data.items()}),
    )


class FakeTransport:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def perform(self, operation, order, at, readiness):
        self.calls.append(
            (
                operation,
                {} if operation is Operation.PING else wire_parameters(operation, order, at),
            )
        )
        if operation is Operation.PING:
            return TransportReply(200, {})
        if self.replies:
            return self.replies.pop(0)
        return TransportReply(
            200,
            {
                "symbol": order.symbol,
                "side": order.side.value,
                "type": "MARKET",
                "origQty": str(order.quantity),
                "clientOrderId": order.client_order_id,
                "orderId": 123,
                "status": "FILLED",
                "transactTime": int(at.timestamp() * 1000),
                "updateTime": int(at.timestamp() * 1000),
            },
            possibly_sent=True,
        )


class FakeOnlyAdapter(ExchangeAdapter):
    __test__ = False

    def _authorize(self, order, proof, risk, strategy, authorization, readiness):
        if type(self._transport) is not FakeTransport:
            return Reason.TESTNET_NOT_AUTHORIZED
        error = _order_error(order, proof, risk, strategy, risk_environment="TEST")
        if error:
            return error
        if (
            not valid(authorization, TestnetExecutionAuthorization)
            or authorization != fixture_auth()
        ):
            return Reason.TESTNET_NOT_AUTHORIZED
        return None


def fixture_auth():
    return record(
        TestnetExecutionAuthorization,
        environment="TESTNET",
        ops_readiness_identity=ContentIdentity.from_text("test-only-ops"),
        qualification_identity=ContentIdentity.from_text("test-only-qualification"),
        observability_identity=ContentIdentity.from_text("test-only-observability"),
        issued_for_policy=ExchangePolicy().content_identity,
    )


def inputs(side=Side.BUY):
    kind = SignalKind.LONG_ENTRY if side is Side.BUY else SignalKind.EXIT
    original = strategy_evaluation(kind)
    strategy = StrategyEvaluation.completed(
        replace(original.provenance, environment=Environment.TEST), kind
    )
    context = RiskEvaluationContext(
        strategy,
        market_context(environment="TEST"),
        empty_portfolio() if side is Side.BUY else open_portfolio(open_position()),
    )
    result = DeterministicRiskEngine(policy()).evaluate(context)
    assert result.status is RiskStatus.APPROVED
    risk = result.decision
    proof = record(
        UpstreamOrderProof,
        symbol="BTCUSDT",
        side=side,
        quantity=Decimal("0.001"),
        risk_decision_id=str(risk.risk_decision_id),
        risk_decision_identity=risk.content_identity,
        strategy_evaluation_identity=strategy.content_identity,
        environment="TESTNET",
        created_at=NOW,
    )
    return request_from_proof(proof), dict(
        proof=proof,
        risk=risk,
        strategy=strategy,
        authorization=fixture_auth(),
        readiness=None,
        submitted_at=NOW,
    )
