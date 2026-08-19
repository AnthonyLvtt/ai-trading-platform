from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp.data.identity import DatasetId, SnapshotId, UniverseSnapshotId
from atp.risk import (
    DeterministicRiskEngine,
    InstrumentClass,
    MarketType,
    OpenPosition,
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    PositionId,
    PositionSide,
    RiskDecision,
    RiskDecisionId,
    RiskEvaluationContext,
    RiskMarketContext,
    RiskPolicy,
    RiskPolicyId,
    RiskReasonCode,
    RiskStatus,
)
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import LogicalTime
from atp.strategy import (
    ReasonCode,
    SignalKind,
    SignalProvenance,
    SmaCrossoverConfig,
    StrategyEvaluation,
    StrategyId,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def strategy_evaluation(kind: SignalKind = SignalKind.LONG_ENTRY) -> StrategyEvaluation:
    provenance = SignalProvenance(
        strategy_id=StrategyId("sma-crossover"),
        strategy_version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
        environment=Environment.BACKTEST,
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:risk-fixture:v1"),
        snapshot_content_identity=ContentIdentity.from_text("snapshot"),
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        lineage_content_identity=ContentIdentity.from_text("lineage"),
        universe_snapshot_id=UniverseSnapshotId("universe:risk-fixture:v1"),
        universe_content_identity=ContentIdentity.from_text("universe"),
        evaluation_time=LogicalTime(NOW),
        symbol="BTCUSDT",
        used_data=(),
    )
    return StrategyEvaluation.completed(provenance, kind)


def policy(*, version: str = "1.0.0") -> RiskPolicy:
    return RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version=version)


def market_context(**changes: object) -> RiskMarketContext:
    values: dict[str, object] = {
        "environment": Environment.BACKTEST.value,
        "instrument_class": InstrumentClass.SPOT,
        "leverage": Decimal(1),
        "margin_enabled": False,
        "market_type": MarketType.SPOT,
        "position_direction": PositionDirection.LONG,
        "symbol": "BTCUSDT",
    }
    values.update(changes)
    return RiskMarketContext(**values)  # type: ignore[arg-type]


def empty_portfolio() -> PortfolioState:
    return PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY)


def open_position(
    *,
    symbol: str = "BTCUSDT",
    position_id: str = "position-1",
    side: PositionSide = PositionSide.LONG,
) -> OpenPosition:
    return OpenPosition(PositionId(position_id), symbol, side)


def open_portfolio(*positions: OpenPosition) -> PortfolioState:
    return PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_OPEN, positions)


def context(
    *,
    signal_kind: SignalKind = SignalKind.LONG_ENTRY,
    strategy: StrategyEvaluation | None = None,
    market: RiskMarketContext | None = None,
    portfolio: PortfolioState | None = None,
) -> RiskEvaluationContext:
    return RiskEvaluationContext(
        strategy_evaluation=strategy or strategy_evaluation(signal_kind),
        market_context=market or market_context(),
        portfolio_state=portfolio or empty_portfolio(),
    )


def evaluate(evaluation_context: RiskEvaluationContext) -> object:
    return DeterministicRiskEngine(policy()).evaluate(evaluation_context)


def test_risk_identifiers_are_typed_and_validated() -> None:
    assert RiskPolicyId("same") != RiskDecisionId("same")
    assert RiskDecisionId("same") != PositionId("same")
    with pytest.raises(ValidationError):
        RiskPolicyId(" risk-v1 ")


def test_policy_v1_is_immutable_and_fixed_to_cto_decision() -> None:
    risk_policy = policy()

    assert risk_policy.allowed_environments == ACTIVE_ENVIRONMENTS
    assert risk_policy.max_positions == 1
    with pytest.raises(FrozenInstanceError):
        risk_policy.max_positions = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RiskPolicy(RiskPolicyId("bad"), "1", ACTIVE_ENVIRONMENTS, 2)


def test_same_input_produces_same_decision() -> None:
    evaluation_context = context()
    engine = DeterministicRiskEngine(policy())

    first = engine.evaluate(evaluation_context)
    second = engine.evaluate(evaluation_context)

    assert first == second
    assert first.content_identity == second.content_identity
    assert first.risk_decision_id == second.risk_decision_id


def test_long_entry_with_known_empty_portfolio_is_approved() -> None:
    result = DeterministicRiskEngine(policy()).evaluate(context())

    assert result.status is RiskStatus.APPROVED
    assert result.reason_code is RiskReasonCode.POLICY_COMPLIANT
    assert result.decision is not None
    assert result.risk_decision_id is not None


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
def test_long_entry_with_any_open_position_is_rejected(symbol: str) -> None:
    result = DeterministicRiskEngine(policy()).evaluate(
        context(portfolio=open_portfolio(open_position(symbol=symbol)))
    )

    assert result.status is RiskStatus.REJECTED
    assert result.reason_code is RiskReasonCode.MAX_POSITIONS_REACHED


def test_no_action_has_explicit_non_economic_result() -> None:
    evaluation_context = RiskEvaluationContext(
        strategy_evaluation=strategy_evaluation(SignalKind.NO_ACTION),
        market_context=None,
        portfolio_state=None,
    )

    result = DeterministicRiskEngine(policy()).evaluate(evaluation_context)

    assert result.status is RiskStatus.NO_DECISION
    assert result.reason_code is RiskReasonCode.STRATEGY_NO_ACTION
    assert result.decision is None
    assert result.risk_decision_id is None


@pytest.mark.parametrize(
    ("portfolio", "status", "reason"),
    [
        (
            empty_portfolio(),
            RiskStatus.REJECTED,
            RiskReasonCode.NO_OPEN_POSITION,
        ),
        (
            open_portfolio(open_position(symbol="ETHUSDT")),
            RiskStatus.REJECTED,
            RiskReasonCode.POSITION_SYMBOL_MISMATCH,
        ),
        (
            open_portfolio(open_position()),
            RiskStatus.APPROVED,
            RiskReasonCode.POLICY_COMPLIANT,
        ),
    ],
)
def test_exit_policy(
    portfolio: PortfolioState,
    status: RiskStatus,
    reason: RiskReasonCode,
) -> None:
    result = DeterministicRiskEngine(policy()).evaluate(
        context(signal_kind=SignalKind.EXIT, portfolio=portfolio)
    )

    assert result.status is status
    assert result.reason_code is reason


@pytest.mark.parametrize(
    ("portfolio", "reason"),
    [
        (
            PortfolioState.create(PortfolioKnowledgeStatus.UNKNOWN),
            RiskReasonCode.PORTFOLIO_STATE_UNKNOWN,
        ),
        (
            PortfolioState.create(PortfolioKnowledgeStatus.INCONSISTENT),
            RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT,
        ),
        (
            PortfolioState.create(
                PortfolioKnowledgeStatus.KNOWN_EMPTY,
                (open_position(),),
            ),
            RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT,
        ),
        (
            PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_OPEN),
            RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT,
        ),
        (
            open_portfolio(open_position(), open_position(position_id="position-2")),
            RiskReasonCode.PORTFOLIO_POLICY_VIOLATION,
        ),
        (
            open_portfolio(open_position(side=PositionSide.SHORT)),
            RiskReasonCode.PORTFOLIO_POLICY_VIOLATION,
        ),
    ],
)
def test_unknown_or_incoherent_portfolio_is_blocked(
    portfolio: PortfolioState,
    reason: RiskReasonCode,
) -> None:
    result = DeterministicRiskEngine(policy()).evaluate(context(portfolio=portfolio))

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is reason


@pytest.mark.parametrize(
    ("environment", "reason"),
    [
        ("production", RiskReasonCode.UNKNOWN_ENVIRONMENT),
        (None, RiskReasonCode.UNKNOWN_ENVIRONMENT),
        (Environment.DRY_RUN.value, RiskReasonCode.ENVIRONMENT_NOT_ACTIVE),
        (Environment.TESTNET.value, RiskReasonCode.ENVIRONMENT_NOT_ACTIVE),
        (Environment.LIVE.value, RiskReasonCode.ENVIRONMENT_NOT_ACTIVE),
    ],
)
def test_unknown_or_inactive_environment_is_blocked(
    environment: str | None,
    reason: RiskReasonCode,
) -> None:
    result = DeterministicRiskEngine(policy()).evaluate(
        context(market=market_context(environment=environment))
    )

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is reason


def test_strategy_and_market_environment_mismatch_is_blocked() -> None:
    result = DeterministicRiskEngine(policy()).evaluate(
        context(market=market_context(environment=Environment.TEST.value))
    )

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.STRATEGY_CONTEXT_INCOMPATIBLE


def test_strategy_and_market_symbol_mismatch_is_blocked() -> None:
    result = DeterministicRiskEngine(policy()).evaluate(
        context(market=market_context(symbol="ETHUSDT"))
    )

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.MARKET_SYMBOL_MISMATCH
    assert result.provenance.market_context is not None
    assert result.provenance.market_context.symbol == "ETHUSDT"
    assert market_context(symbol="ETHUSDT").content_identity != market_context().content_identity


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"market_type": "SPOT"}, RiskReasonCode.MARKET_CONTEXT_INCOMPLETE),
        ({"position_direction": "LONG"}, RiskReasonCode.MARKET_CONTEXT_INCOMPLETE),
        ({"margin_enabled": 0}, RiskReasonCode.MARKET_CONTEXT_INCOMPLETE),
        ({"leverage": "1"}, RiskReasonCode.MARKET_CONTEXT_INCOMPLETE),
        ({"instrument_class": "SPOT"}, RiskReasonCode.MARKET_CONTEXT_INCOMPLETE),
        ({"symbol": 7}, RiskReasonCode.MARKET_CONTEXT_INCOMPLETE),
        ({"environment": 7}, RiskReasonCode.UNKNOWN_ENVIRONMENT),
    ],
)
def test_malformed_market_runtime_values_are_blocked_deterministically(
    changes: dict[str, object],
    reason: RiskReasonCode,
) -> None:
    evaluation_context = context(market=market_context(**changes))
    engine = DeterministicRiskEngine(policy())

    first = engine.evaluate(evaluation_context)
    second = engine.evaluate(evaluation_context)

    assert first == second
    assert first.status is RiskStatus.BLOCKED
    assert first.reason_code is reason
    assert first.risk_decision_id is not None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"market_type": MarketType.FUTURES}, RiskReasonCode.MARKET_TYPE_NOT_SPOT),
        (
            {"position_direction": PositionDirection.SHORT},
            RiskReasonCode.POSITION_DIRECTION_NOT_LONG,
        ),
        ({"margin_enabled": True}, RiskReasonCode.MARGIN_NOT_ALLOWED),
        ({"leverage": Decimal("2")}, RiskReasonCode.LEVERAGE_NOT_ALLOWED),
        (
            {"instrument_class": InstrumentClass.FUTURES},
            RiskReasonCode.INSTRUMENT_CLASS_NOT_SPOT,
        ),
    ],
)
def test_known_prohibited_market_context_is_rejected(
    changes: dict[str, object],
    reason: RiskReasonCode,
) -> None:
    result = DeterministicRiskEngine(policy()).evaluate(context(market=market_context(**changes)))

    assert result.status is RiskStatus.REJECTED
    assert result.reason_code is reason


@pytest.mark.parametrize(
    "changes",
    [
        {"market_type": None},
        {"market_type": MarketType.UNKNOWN},
        {"position_direction": None},
        {"position_direction": PositionDirection.UNKNOWN},
        {"margin_enabled": None},
        {"leverage": None},
        {"leverage": Decimal("NaN")},
        {"instrument_class": None},
        {"instrument_class": InstrumentClass.UNKNOWN},
    ],
)
def test_missing_or_unknown_market_proof_is_blocked(changes: dict[str, object]) -> None:
    result = DeterministicRiskEngine(policy()).evaluate(context(market=market_context(**changes)))

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.MARKET_CONTEXT_INCOMPLETE


def test_missing_or_blocked_strategy_input_is_blocked() -> None:
    missing = DeterministicRiskEngine(policy()).evaluate(
        RiskEvaluationContext(None, market_context(), empty_portfolio())
    )
    blocked_strategy = StrategyEvaluation.blocked(
        strategy_evaluation().provenance,
        ReasonCode.DATA_CONTRACT_UNSATISFIED,
    )
    blocked = DeterministicRiskEngine(policy()).evaluate(context(strategy=blocked_strategy))

    assert missing.status is RiskStatus.BLOCKED
    assert missing.reason_code is RiskReasonCode.STRATEGY_INPUT_INCOMPLETE
    assert blocked.status is RiskStatus.BLOCKED
    assert blocked.reason_code is RiskReasonCode.STRATEGY_INPUT_INCOMPLETE


@pytest.mark.parametrize(
    "portfolio",
    [
        PortfolioState("KNOWN_EMPTY", ()),  # type: ignore[arg-type]
        PortfolioState(PortfolioKnowledgeStatus.KNOWN_EMPTY, []),  # type: ignore[arg-type]
        PortfolioState(PortfolioKnowledgeStatus.KNOWN_OPEN, ("position",)),  # type: ignore[arg-type]
    ],
)
def test_malformed_portfolio_runtime_values_are_blocked_deterministically(
    portfolio: PortfolioState,
) -> None:
    evaluation_context = context(portfolio=portfolio)
    engine = DeterministicRiskEngine(policy())

    first = engine.evaluate(evaluation_context)
    second = engine.evaluate(evaluation_context)

    assert first == second
    assert first.status is RiskStatus.BLOCKED
    assert first.reason_code is RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT


def test_malformed_position_enum_is_blocked_without_canonicalization_error() -> None:
    position = open_position()
    object.__setattr__(position, "side", "LONG")
    portfolio = PortfolioState(PortfolioKnowledgeStatus.KNOWN_OPEN, (position,))

    result = DeterministicRiskEngine(policy()).evaluate(context(portfolio=portfolio))

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT


def test_malformed_top_level_runtime_evidence_is_blocked() -> None:
    engine = DeterministicRiskEngine(policy())
    malformed_market = RiskEvaluationContext(
        strategy_evaluation=strategy_evaluation(),
        market_context={"symbol": "BTCUSDT"},  # type: ignore[arg-type]
        portfolio_state=empty_portfolio(),
    )
    malformed_strategy = RiskEvaluationContext(
        strategy_evaluation="LONG_ENTRY",  # type: ignore[arg-type]
        market_context=market_context(),
        portfolio_state=empty_portfolio(),
    )

    market_result = engine.evaluate(malformed_market)
    strategy_result = engine.evaluate(malformed_strategy)

    assert market_result.status is RiskStatus.BLOCKED
    assert market_result.reason_code is RiskReasonCode.MARKET_CONTEXT_INCOMPLETE
    assert strategy_result.status is RiskStatus.BLOCKED
    assert strategy_result.reason_code is RiskReasonCode.STRATEGY_INPUT_INCOMPLETE


def test_tampered_strategy_identity_is_blocked() -> None:
    strategy = strategy_evaluation()
    assert strategy.signal is not None
    object.__setattr__(strategy.signal, "content_identity", ContentIdentity.from_text("tampered"))

    result = DeterministicRiskEngine(policy()).evaluate(context(strategy=strategy))

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.STRATEGY_INPUT_NOT_REPRODUCIBLE


def test_malformed_nested_strategy_provenance_is_blocked_without_crashing() -> None:
    strategy = strategy_evaluation()
    object.__setattr__(strategy, "provenance", "invalid-provenance")

    result = DeterministicRiskEngine(policy()).evaluate(context(strategy=strategy))

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.STRATEGY_INPUT_NOT_REPRODUCIBLE


def test_malformed_evaluation_context_is_blocked_without_crashing() -> None:
    result = DeterministicRiskEngine(policy()).evaluate("invalid-context")  # type: ignore[arg-type]

    assert result.status is RiskStatus.BLOCKED
    assert result.reason_code is RiskReasonCode.STRATEGY_INPUT_INCOMPLETE


def test_decision_preserves_complete_strategy_and_risk_provenance() -> None:
    strategy = strategy_evaluation()

    result = DeterministicRiskEngine(policy()).evaluate(context(strategy=strategy))

    assert result.provenance.strategy_evaluation_id == strategy.strategy_evaluation_id
    assert result.provenance.strategy_evaluation_identity == strategy.content_identity
    assert result.provenance.strategy_decision_id == strategy.signal.strategy_decision_id  # type: ignore[union-attr]
    assert result.provenance.strategy_id == strategy.provenance.strategy_id
    assert result.provenance.strategy_version == strategy.provenance.strategy_version
    assert result.provenance.risk_policy_identity == policy().content_identity
    assert result.provenance.market_context == market_context()
    assert result.provenance.market_context_identity == market_context().content_identity
    assert result.provenance.portfolio_state_identity == empty_portfolio().content_identity


def test_policy_or_portfolio_change_changes_risk_identity() -> None:
    evaluation_context = context()
    first = DeterministicRiskEngine(policy(version="1.0.0")).evaluate(evaluation_context)
    changed_policy = DeterministicRiskEngine(policy(version="1.0.1")).evaluate(evaluation_context)
    changed_portfolio = DeterministicRiskEngine(policy()).evaluate(
        context(portfolio=open_portfolio(open_position()))
    )

    assert first.content_identity != changed_policy.content_identity
    assert first.content_identity != changed_portfolio.content_identity


def test_portfolio_identity_is_independent_of_input_collection_order() -> None:
    first = open_position(position_id="position-1")
    second = open_position(position_id="position-2", symbol="ETHUSDT")

    assert (
        open_portfolio(first, second).content_identity
        == open_portfolio(second, first).content_identity
    )


def test_risk_models_expose_no_order_sizing_or_quantity() -> None:
    forbidden = {"order", "quantity", "position_size", "size", "notional"}
    model_fields = {
        field.name
        for model in (RiskDecision, RiskEvaluationContext, RiskMarketContext, OpenPosition)
        for field in fields(model)
    }

    assert model_fields.isdisjoint(forbidden)


def test_engine_has_no_system_clock_network_exchange_oms_or_ai_dependency() -> None:
    source = inspect.getsource(inspect.getmodule(DeterministicRiskEngine))

    assert "datetime.now" not in source
    assert "socket" not in source
    assert "atp.exchange" not in source
    assert "atp.oms" not in source
    assert "openai" not in source.lower()


def test_no_decision_cannot_be_constructed_as_economic_decision() -> None:
    result = DeterministicRiskEngine(policy()).evaluate(context(signal_kind=SignalKind.NO_ACTION))

    with pytest.raises(ValidationError):
        RiskDecision.create(
            status=RiskStatus.NO_DECISION,
            reason_code=RiskReasonCode.STRATEGY_NO_ACTION,
            provenance=result.provenance,
        )
