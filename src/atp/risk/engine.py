from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from atp.risk.identity import PositionId
from atp.risk.model import (
    InstrumentClass,
    MarketType,
    OpenPosition,
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
from atp.shared.identity import ContentIdentity
from atp.strategy.identity import StrategyDecisionId, StrategyEvaluationId, StrategyId
from atp.strategy.model import (
    EvaluationStatus,
    SignalKind,
    SignalProvenance,
    StrategyEvaluation,
    StrategySignal,
)


@dataclass(frozen=True, slots=True)
class RiskEvaluationContext:
    strategy_evaluation: StrategyEvaluation | None
    market_context: RiskMarketContext | None
    portfolio_state: PortfolioState | None


@dataclass(frozen=True, slots=True)
class DeterministicRiskEngine:
    policy: RiskPolicy

    def evaluate(self, context: RiskEvaluationContext) -> RiskProcessingResult:
        if not isinstance(context, RiskEvaluationContext):
            context = RiskEvaluationContext(None, None, None)
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
        if not isinstance(market, RiskMarketContext):
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
        if (
            not isinstance(market.symbol, str)
            or not market.symbol
            or market.symbol.strip() != market.symbol
            or not isinstance(market.market_type, MarketType)
            or not isinstance(market.position_direction, PositionDirection)
            or not isinstance(market.margin_enabled, bool)
            or not isinstance(market.leverage, Decimal)
            or not market.leverage_is_finite
            or not isinstance(market.instrument_class, InstrumentClass)
        ):
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.MARKET_CONTEXT_INCOMPLETE,
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
        if market.symbol != evaluation.provenance.symbol:
            return self._result(
                RiskStatus.BLOCKED,
                RiskReasonCode.MARKET_SYMBOL_MISMATCH,
                context,
            )
        if (
            market.market_type is MarketType.UNKNOWN
            or market.position_direction is PositionDirection.UNKNOWN
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
        not isinstance(evaluation, StrategyEvaluation)
        or evaluation.status is not EvaluationStatus.COMPLETED
        or evaluation.signal is None
    ):
        return RiskReasonCode.STRATEGY_INPUT_INCOMPLETE
    try:
        replace(evaluation.provenance.configuration)
        replace(evaluation.provenance)
        replace(evaluation.signal)
        replace(evaluation)
    except Exception:  # Malformed external Strategy evidence must become a typed block.
        return RiskReasonCode.STRATEGY_INPUT_NOT_REPRODUCIBLE
    return None


def _validate_portfolio(
    portfolio: PortfolioState | None,
    max_positions: int,
) -> RiskReasonCode | None:
    if portfolio is None:
        return RiskReasonCode.PORTFOLIO_STATE_UNKNOWN
    if not isinstance(portfolio, PortfolioState):
        return RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT
    if not isinstance(portfolio.knowledge_status, PortfolioKnowledgeStatus) or not isinstance(
        portfolio.positions,
        tuple,
    ):
        return RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT
    if not all(isinstance(position, OpenPosition) for position in portfolio.positions):
        return RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT
    if any(
        not isinstance(position.position_id, PositionId)
        or not isinstance(position.symbol, str)
        or not position.symbol
        or position.symbol.strip() != position.symbol
        or not isinstance(position.side, PositionSide)
        for position in portfolio.positions
    ):
        return RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT
    if portfolio.knowledge_status is PortfolioKnowledgeStatus.UNKNOWN:
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


def _parse_environment(raw: object) -> Environment | None:
    if not isinstance(raw, str):
        return None
    try:
        return Environment(raw)
    except ValueError:
        return None


def _provenance(policy: RiskPolicy, context: RiskEvaluationContext) -> RiskProvenance:
    raw_evaluation = context.strategy_evaluation
    evaluation = raw_evaluation if isinstance(raw_evaluation, StrategyEvaluation) else None
    raw_signal = None if evaluation is None else evaluation.signal
    signal = raw_signal if isinstance(raw_signal, StrategySignal) else None
    raw_strategy_provenance = None if evaluation is None else evaluation.provenance
    strategy_provenance = (
        raw_strategy_provenance if isinstance(raw_strategy_provenance, SignalProvenance) else None
    )
    raw_market = context.market_context
    market = raw_market if isinstance(raw_market, RiskMarketContext) else None
    raw_portfolio = context.portfolio_state
    return RiskProvenance(
        strategy_evaluation_id=None
        if evaluation is None
        or not isinstance(evaluation.strategy_evaluation_id, StrategyEvaluationId)
        else evaluation.strategy_evaluation_id,
        strategy_evaluation_identity=None
        if evaluation is None or not isinstance(evaluation.content_identity, ContentIdentity)
        else evaluation.content_identity,
        strategy_decision_id=None
        if signal is None or not isinstance(signal.strategy_decision_id, StrategyDecisionId)
        else signal.strategy_decision_id,
        strategy_signal_identity=None
        if signal is None or not isinstance(signal.content_identity, ContentIdentity)
        else signal.content_identity,
        strategy_id=None
        if strategy_provenance is None
        or not isinstance(strategy_provenance.strategy_id, StrategyId)
        else strategy_provenance.strategy_id,
        strategy_version=None
        if strategy_provenance is None or not isinstance(strategy_provenance.strategy_version, str)
        else strategy_provenance.strategy_version,
        strategy_provenance_identity=_safe_strategy_provenance_identity(strategy_provenance),
        risk_policy_id=policy.policy_id,
        risk_policy_version=policy.version,
        risk_policy_identity=policy.content_identity,
        environment=None
        if market is None or not isinstance(market.environment, str)
        else market.environment,
        market_context=market,
        market_context_identity=_runtime_evidence_identity("market_context", raw_market),
        portfolio_state_identity=_runtime_evidence_identity("portfolio_state", raw_portfolio),
    )


def _runtime_evidence_identity(kind: str, evidence: object) -> ContentIdentity | None:
    if evidence is None:
        return None
    if isinstance(evidence, RiskMarketContext | PortfolioState):
        return evidence.content_identity
    evidence_type = type(evidence)
    return ContentIdentity.from_canonical(
        {
            "invalid_evidence": kind,
            "invalid_type": f"{evidence_type.__module__}.{evidence_type.__qualname__}",
        }
    )


def _safe_strategy_provenance_identity(
    provenance: SignalProvenance | None,
) -> ContentIdentity | None:
    if provenance is None:
        return None
    try:
        return provenance.content_identity
    except Exception:  # Provenance creation is part of the same fail-closed boundary.
        return ContentIdentity.from_canonical({"invalid_strategy_provenance": True})
