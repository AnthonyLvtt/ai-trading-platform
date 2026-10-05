from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from atp.exchange.contracts import BTC_EUR
from atp.exchange.execution import (
    EconomicExecutionPort,
    ExecutionError,
    ExecutionLedger,
    OrderIntent,
    OrderSide,
    OrderType,
    SubmissionState,
)
from atp.exchange.kraken.execution import DisabledKrakenEconomicTransport
from atp.risk.engine import DeterministicRiskEngine, RiskEvaluationContext
from atp.risk.model import (
    InstrumentClass,
    MarketType,
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    RiskMarketContext,
)
from atp.risk.policy import RISK_POLICY_V1
from atp.shared.environment import Environment
from atp.strategy.model import SignalKind
from tests.unit.test_strategy_baseline import context, strategy


def approved(signal_kind: SignalKind):
    evaluation = strategy().evaluate(context(signal_kind=signal_kind))
    market = RiskMarketContext(
        symbol=BTC_EUR.symbol,
        market_type=MarketType.SPOT,
        position_direction=PositionDirection.LONG,
        margin_enabled=False,
        leverage=Decimal("1"),
        instrument_class=InstrumentClass.SPOT,
        environment=Environment.BACKTEST.value,
    )
    portfolio = PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY)
    risk = DeterministicRiskEngine(RISK_POLICY_V1).evaluate(
        RiskEvaluationContext(evaluation, market, portfolio)
    )
    assert risk.decision is not None
    return evaluation, risk.decision


def test_buy_intent_requires_exact_strategy_and_approved_risk_binding() -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    intent = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )
    duplicate = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )
    assert intent.instrument == BTC_EUR
    assert intent.idempotency_key == duplicate.idempotency_key


def test_side_and_order_fields_fail_closed() -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    with pytest.raises(ExecutionError, match="STRATEGY_SIDE_MISMATCH"):
        OrderIntent.create(
            strategy_evaluation=evaluation,
            risk_decision=decision,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.001"),
        )
    with pytest.raises(ExecutionError, match="MARKET_PRICE_FORBIDDEN"):
        OrderIntent.create(
            strategy_evaluation=evaluation,
            risk_decision=decision,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.001"),
            limit_price=Decimal("10"),
        )
    with pytest.raises(ExecutionError, match="ORDER_FIELDS_INVALID"):
        OrderIntent.create(
            strategy_evaluation=evaluation,
            risk_decision=decision,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),
        )


def test_tampered_risk_binding_is_rejected() -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    altered = replace(
        decision,
        provenance=replace(
            decision.provenance,
            strategy_evaluation_identity=decision.provenance.risk_policy_identity,
        ),
    )
    with pytest.raises(ExecutionError, match="RISK_BINDING_INVALID"):
        OrderIntent.create(
            strategy_evaluation=evaluation,
            risk_decision=altered,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.001"),
        )


def test_ledger_is_append_only_idempotent_and_models_ambiguity(tmp_path: Path) -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    intent = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        limit_price=Decimal("50000"),
    )
    ledger = ExecutionLedger(tmp_path / "execution.sqlite")
    ledger.prepare(intent)
    assert ledger.current_state(intent.idempotency_key) is SubmissionState.PREPARED
    ledger.transition(intent, SubmissionState.ATTEMPT_STARTED, reason_code="ATTEMPT_RESERVED")
    ledger.transition(intent, SubmissionState.UNKNOWN, reason_code="OUTCOME_AMBIGUOUS")
    assert ledger.current_state(intent.idempotency_key) is SubmissionState.UNKNOWN
    ledger.transition(intent, SubmissionState.RECONCILED, reason_code="READ_ONLY_RECONCILED")
    assert ledger.current_state(intent.idempotency_key) is SubmissionState.RECONCILED
    with pytest.raises(ExecutionError, match="IDEMPOTENCY_KEY_ALREADY_RECORDED"):
        ledger.prepare(intent)
    with pytest.raises(ExecutionError, match="LEDGER_TRANSITION_INVALID"):
        ledger.transition(intent, SubmissionState.ATTEMPT_STARTED, reason_code="INVALID_RETRY")


def test_economic_transport_is_disabled_and_has_no_network_surface() -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    intent = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )
    for port in (EconomicExecutionPort(), DisabledKrakenEconomicTransport()):
        with pytest.raises(ExecutionError, match="KRAKEN_ECONOMIC_EXECUTION_NOT_QUALIFIED"):
            port.submit(intent)

    path = Path("src/atp/exchange/kraken/execution.py")
    tree = ast.parse(path.read_text())
    forbidden = ("socket", "http", "urllib", "requests", "httpx", "ssl")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith(forbidden) for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(forbidden)
