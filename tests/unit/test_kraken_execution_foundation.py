from __future__ import annotations

import ast
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier

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
from atp.risk.identity import PositionId, RiskPolicyId
from atp.risk.model import (
    InstrumentClass,
    MarketType,
    OpenPosition,
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    PositionSide,
    RiskMarketContext,
)
from atp.risk.policy import RiskPolicy
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from atp.strategy.model import SignalKind
from tests.unit.test_strategy_baseline import context, snapshot, strategy


def approved(signal_kind: SignalKind):
    closes = ("3", "2", "1", "4") if signal_kind is SignalKind.LONG_ENTRY else ("1", "2", "3", "0")
    evaluation = strategy().evaluate(context(snapshot(closes)))
    market = RiskMarketContext(
        symbol=BTC_EUR.symbol,
        market_type=MarketType.SPOT,
        position_direction=PositionDirection.LONG,
        margin_enabled=False,
        leverage=Decimal("1"),
        instrument_class=InstrumentClass.SPOT,
        environment=Environment.BACKTEST.value,
    )
    portfolio = (
        PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY)
        if signal_kind is SignalKind.LONG_ENTRY
        else PortfolioState.create(
            PortfolioKnowledgeStatus.KNOWN_OPEN,
            (
                OpenPosition(
                    PositionId("position:fixture"),
                    BTC_EUR.symbol,
                    PositionSide.LONG,
                ),
            ),
        )
    )
    policy = RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    risk = DeterministicRiskEngine(policy).evaluate(
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


def test_exit_signal_builds_sell_intent() -> None:
    evaluation, decision = approved(SignalKind.EXIT)
    intent = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )
    assert intent.side is OrderSide.SELL


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
    object.__setattr__(
        decision.provenance,
        "strategy_evaluation_identity",
        decision.provenance.risk_policy_identity,
    )
    with pytest.raises(ExecutionError, match="RISK_BINDING_INVALID"):
        OrderIntent.create(
            strategy_evaluation=evaluation,
            risk_decision=decision,
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
    ledger.transition(
        intent,
        SubmissionState.ATTEMPT_STARTED,
        reason_code="ATTEMPT_RESERVED",
    )
    ledger.transition(intent, SubmissionState.UNKNOWN, reason_code="OUTCOME_AMBIGUOUS")
    assert ledger.current_state(intent.idempotency_key) is SubmissionState.UNKNOWN
    ledger.transition(
        intent,
        SubmissionState.RECONCILED,
        reason_code="READ_ONLY_RECONCILED",
    )
    assert ledger.current_state(intent.idempotency_key) is SubmissionState.RECONCILED
    with pytest.raises(ExecutionError, match="IDEMPOTENCY_KEY_ALREADY_RECORDED"):
        ledger.prepare(intent)
    with pytest.raises(ExecutionError, match="LEDGER_TRANSITION_INVALID"):
        ledger.transition(
            intent,
            SubmissionState.ATTEMPT_STARTED,
            reason_code="INVALID_RETRY",
        )


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


def test_tampered_intent_is_rejected_at_consumption_boundary() -> None:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    intent = OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )
    object.__setattr__(
        intent,
        "risk_decision_identity",
        ContentIdentity.from_text("tampered-risk-decision"),
    )
    with pytest.raises(ExecutionError, match="EXECUTION_INTENT_INVALID"):
        DisabledKrakenEconomicTransport().submit(intent)


def _order_intent() -> OrderIntent:
    evaluation, decision = approved(SignalKind.LONG_ENTRY)
    return OrderIntent.create(
        strategy_evaluation=evaluation,
        risk_decision=decision,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.001"),
    )


def test_competing_outcomes_cannot_both_commit(tmp_path: Path) -> None:
    intent = _order_intent()
    read_barrier = Barrier(2)

    class RacingLedger(ExecutionLedger):
        def current_state(self, key: ContentIdentity) -> SubmissionState | None:
            state = super().current_state(key)
            if state is SubmissionState.ATTEMPT_STARTED:
                read_barrier.wait(timeout=5)
            return state

    ledger = RacingLedger(tmp_path / "execution.sqlite")
    ledger.prepare(intent)
    ledger.transition(intent, SubmissionState.ATTEMPT_STARTED, reason_code="ATTEMPT_RESERVED")
    start = Barrier(2)

    def record(target: SubmissionState) -> str:
        start.wait(timeout=5)
        try:
            ledger.transition(intent, target, reason_code="OBSERVED_OUTCOME")
        except ExecutionError as error:
            return str(error)
        return "COMMITTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(record, SubmissionState.ACKNOWLEDGED)
        second = pool.submit(record, SubmissionState.UNKNOWN)
        assert sorted((first.result(timeout=10), second.result(timeout=10))) == [
            "COMMITTED",
            "LEDGER_TRANSITION_INVALID",
        ]
    assert ExecutionLedger(tmp_path / "execution.sqlite").current_state(intent.idempotency_key) in {
        SubmissionState.ACKNOWLEDGED,
        SubmissionState.UNKNOWN,
    }


@pytest.mark.parametrize(
    ("state", "previous"),
    [
        ("ACKNOWLEDGED", "PREPARED"),
        ("UNKNOWN", "ATTEMPT_STARTED"),
        ("INVALID", "ACKNOWLEDGED"),
    ],
)
def test_tampered_or_divergent_history_fails_closed(
    tmp_path: Path, state: str, previous: str
) -> None:
    intent = _order_intent()
    path = tmp_path / "execution.sqlite"
    ledger = ExecutionLedger(path)
    ledger.prepare(intent)
    ledger.transition(intent, SubmissionState.ATTEMPT_STARTED, reason_code="ATTEMPT_RESERVED")
    if state == "UNKNOWN":
        ledger.transition(intent, SubmissionState.ACKNOWLEDGED, reason_code="OBSERVED_OUTCOME")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO execution_transitions "
            "(idempotency_key, state, previous_state, reason_code) VALUES (?, ?, ?, ?)",
            (str(intent.idempotency_key), state, previous, "INJECTED_ROW"),
        )
    with pytest.raises(ExecutionError, match="LEDGER_HISTORY_INVALID"):
        ledger.current_state(intent.idempotency_key)
    with pytest.raises(ExecutionError, match="LEDGER_HISTORY_INVALID"):
        ledger.transition(intent, SubmissionState.RECONCILED, reason_code="READ_ONLY_RECONCILED")


def test_invalid_transition_target_and_reason_fail_closed(tmp_path: Path) -> None:
    intent = _order_intent()
    ledger = ExecutionLedger(tmp_path / "execution.sqlite")
    ledger.prepare(intent)
    with pytest.raises(ExecutionError, match="LEDGER_INPUT_INVALID"):
        ledger.transition(intent, "ATTEMPT_STARTED", reason_code="ATTEMPT_RESERVED")  # type: ignore[arg-type]
    with pytest.raises(ExecutionError, match="LEDGER_REASON_INVALID"):
        ledger.transition(intent, SubmissionState.ATTEMPT_STARTED, reason_code=None)  # type: ignore[arg-type]
    assert ledger.current_state(intent.idempotency_key) is SubmissionState.PREPARED
