from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from atp.risk.model import (
    InstrumentClass,
    MarketType,
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    PositionSide,
    RiskDecision,
    RiskMarketContext,
    RiskProcessingResult,
    RiskProvenance,
    RiskReasonCode,
    RiskStatus,
)
from atp.risk.policy import RiskPolicy
from atp.shared.environment import Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.strategy.model import EvaluationStatus, SignalKind, StrategyEvaluation


@dataclass(frozen=True, slots=True)
class RiskEvaluationContext:
    strategy_evaluation: StrategyEvaluation | None
    market_context: RiskMarketContext | None
    portfolio_state: PortfolioState | None


@dataclass(frozen=True, slots=True)
class DeterministicRiskEngine:
    policy: RiskPolicy

    def evaluate(self, context: RiskEvaluationContext) -> RiskProcessingResult:
        strategy_reason = _validate_strategy(context.strategy_evaluation)
        if strategy_reason is not None:
            return self._result(RiskStatus.BLOCKED, strategy_reason, context)

        evaluation = context.strategy_evaluation
        assert evaluation is not None
        signal = evaluation.signal
        assert signal is not None
        if signal.kind is SignalKind.NO_ACTION:
            return self._result(
                RiskStatus.NO_DECISION,
                RiskReasonCode.STRATEGY_NO_ACTION,
                context,
            )

        market_result = self._evaluate_market_context(context, evaluation)
        if market_result is not None:
            return market_result

        portfolio_reason = _validate_portfolio(context.portfolio_state, self.policy.max_positions)
        if portfolio_reason is not None:
            return self._result(RiskStatus.BLOCKED, portfolio_reason, context)

        portfolio = context.portfolio_state
        assert portfolio is not None
        if signal.kind is SignalKind.LONG_ENTRY:
            if portfolio.positions:
                return self._result(
                    RiskStatus.REJECTED,
                    RiskReasonCode.MAX_POSITIONS_REACHED,
                    context,
                )
            return self._result(
                RiskStatus.APPROVED,
                RiskReasonCode.POLICY_COMPLIANT,
                context,
            )

        if not portfolio.positions:
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.NO_OPEN_POSITION,
                context,
            )
        if portfolio.positions[0].symbol != evaluation.provenance.symbol:
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.POSITION_SYMBOL_MISMATCH,
                context,
            )
        return self._result(
            RiskStatus.APPROVED,
            RiskReasonCode.POLICY_COMPLIANT,
            context,
        )

    def _evaluate_market_context(
        self,
        context: RiskEvaluationContext,
        evaluation: StrategyEvaluation,
    ) -> RiskProcessingResult | None:
        market = context.market_context
        if market is None:
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.MARKET_CONTEXT_INCOMPLETE,
                context,
            )
        environment = _parse_environment(market.environment)
        if environment is None:
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.UNKNOWN_ENVIRONMENT,
                context,
            )
        if environment not in self.policy.allowed_environments:
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.ENVIRONMENT_NOT_ACTIVE,
                context,
            )
        if environment is not evaluation.provenance.environment:
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.STRATEGY_CONTEXT_INCOMPATIBLE,
                context,
            )
        if (
            market.market_type is None
            or market.market_type is MarketType.UNKNOWN
            or market.position_direction is None
            or market.position_direction is PositionDirection.UNKNOWN
            or market.margin_enabled is None
            or not market.leverage_is_finite
            or market.instrument_class is None
            or market.instrument_class is InstrumentClass.UNKNOWN
        ):
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.MARKET_CONTEXT_INCOMPLETE,
                context,
            )
        if market.market_type is not MarketType.SPOT:
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.MARKET_TYPE_NOT_SPOT,
                context,
            )
        if market.position_direction is not PositionDirection.LONG:
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.POSITION_DIRECTION_NOT_LONG,
                context,
            )
        if market.margin_enabled:
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.MARGIN_NOT_ALLOWED,
                context,
            )
        if market.leverage != Decimal(1):
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.LEVERAGE_NOT_ALLOWED,
                context,
            )
        if market.instrument_class is not InstrumentClass.SPOT:
            return self._result(
                RiskStatus.REJECTED,
                RiskReasonCode.INSTRUMENT_CLASS_NOT_SPOT,
                context,
            )
        return None

    def _result(
        self,
        status: RiskStatus,
        reason_code: RiskReasonCode,
        context: RiskEvaluationContext,
    ) -> RiskProcessingResult:
        provenance = _provenance(self.policy, context)
        decision = (
            None
            if status is RiskStatus.NO_DECISION
            else RiskDecision.create(
                status=status,
                reason_code=reason_code,
                provenance=provenance,
            )
        )
        content_identity = ContentIdentity.from_canonical(
            {
                "decision_identity": None if decision is None else str(decision.content_identity),
                "provenance": provenance.canonical_value(),
                "reason_code": reason_code.value,
                "status": status.value,
            }
        )
        return RiskProcessingResult(
            status=status,
            reason_code=reason_code,
            provenance=provenance,
            decision=decision,
            content_identity=content_identity,
        )


def _validate_strategy(evaluation: StrategyEvaluation | None) -> RiskReasonCode | None:
    if (
        evaluation is None
        or evaluation.status is not EvaluationStatus.COMPLETED
        or evaluation.signal is None
    ):
        return RiskReasonCode.STRATEGY_INPUT_INCOMPLETE
    try:
        replace(evaluation.provenance.configuration)
        replace(evaluation.provenance)
        replace(evaluation.signal)
        replace(evaluation)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return RiskReasonCode.STRATEGY_INPUT_NOT_REPRODUCIBLE
    return None


def _validate_portfolio(
    portfolio: PortfolioState | None,
    max_positions: int,
) -> RiskReasonCode | None:
    if portfolio is None or portfolio.knowledge_status is PortfolioKnowledgeStatus.UNKNOWN:
        return RiskReasonCode.PORTFOLIO_STATE_UNKNOWN
    if portfolio.knowledge_status is PortfolioKnowledgeStatus.INCONSISTENT:
        return RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT
    if (
        portfolio.knowledge_status is PortfolioKnowledgeStatus.KNOWN_EMPTY and portfolio.positions
    ) or (
        portfolio.knowledge_status is PortfolioKnowledgeStatus.KNOWN_OPEN
        and not portfolio.positions
    ):
        return RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT
    if len(portfolio.positions) > max_positions:
        return RiskReasonCode.PORTFOLIO_POLICY_VIOLATION
    position_ids = {position.position_id for position in portfolio.positions}
    if len(position_ids) != len(portfolio.positions) or any(
        position.side is not PositionSide.LONG for position in portfolio.positions
    ):
        return RiskReasonCode.PORTFOLIO_POLICY_VIOLATION
    return None


def _parse_environment(raw: str | None) -> Environment | None:
    if raw is None:
        return None
    try:
        return Environment(raw)
    except ValueError:
        return None


def _provenance(policy: RiskPolicy, context: RiskEvaluationContext) -> RiskProvenance:
    evaluation = context.strategy_evaluation
    signal = None if evaluation is None else evaluation.signal
    strategy_provenance = None if evaluation is None else evaluation.provenance
    market = context.market_context
    portfolio = context.portfolio_state
    return RiskProvenance(
        strategy_evaluation_id=None if evaluation is None else evaluation.strategy_evaluation_id,
        strategy_evaluation_identity=None if evaluation is None else evaluation.content_identity,
        strategy_decision_id=None if signal is None else signal.strategy_decision_id,
        strategy_signal_identity=None if signal is None else signal.content_identity,
        strategy_id=None if strategy_provenance is None else strategy_provenance.strategy_id,
        strategy_version=None
        if strategy_provenance is None
        else strategy_provenance.strategy_version,
        strategy_provenance_identity=None
        if strategy_provenance is None
        else strategy_provenance.content_identity,
        risk_policy_id=policy.policy_id,
        risk_policy_version=policy.version,
        risk_policy_identity=policy.content_identity,
        environment=None if market is None else market.environment,
        market_context=market,
        market_context_identity=None if market is None else market.content_identity,
        portfolio_state_identity=None if portfolio is None else portfolio.content_identity,
    )
