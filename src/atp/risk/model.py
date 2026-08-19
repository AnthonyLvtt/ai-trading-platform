from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from atp.risk.identity import PositionId, RiskDecisionId, RiskPolicyId
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.strategy.identity import StrategyDecisionId, StrategyEvaluationId, StrategyId


class RiskStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    NO_DECISION = "NO_DECISION"


class RiskReasonCode(StrEnum):
    POLICY_COMPLIANT = "POLICY_COMPLIANT"
    STRATEGY_NO_ACTION = "STRATEGY_NO_ACTION"
    STRATEGY_INPUT_INCOMPLETE = "STRATEGY_INPUT_INCOMPLETE"
    STRATEGY_INPUT_NOT_REPRODUCIBLE = "STRATEGY_INPUT_NOT_REPRODUCIBLE"
    STRATEGY_CONTEXT_INCOMPATIBLE = "STRATEGY_CONTEXT_INCOMPATIBLE"
    UNKNOWN_ENVIRONMENT = "UNKNOWN_ENVIRONMENT"
    ENVIRONMENT_NOT_ACTIVE = "ENVIRONMENT_NOT_ACTIVE"
    MARKET_CONTEXT_INCOMPLETE = "MARKET_CONTEXT_INCOMPLETE"
    MARKET_TYPE_NOT_SPOT = "MARKET_TYPE_NOT_SPOT"
    POSITION_DIRECTION_NOT_LONG = "POSITION_DIRECTION_NOT_LONG"
    MARGIN_NOT_ALLOWED = "MARGIN_NOT_ALLOWED"
    LEVERAGE_NOT_ALLOWED = "LEVERAGE_NOT_ALLOWED"
    INSTRUMENT_CLASS_NOT_SPOT = "INSTRUMENT_CLASS_NOT_SPOT"
    PORTFOLIO_STATE_UNKNOWN = "PORTFOLIO_STATE_UNKNOWN"
    PORTFOLIO_STATE_INCONSISTENT = "PORTFOLIO_STATE_INCONSISTENT"
    PORTFOLIO_POLICY_VIOLATION = "PORTFOLIO_POLICY_VIOLATION"
    MAX_POSITIONS_REACHED = "MAX_POSITIONS_REACHED"
    NO_OPEN_POSITION = "NO_OPEN_POSITION"
    POSITION_SYMBOL_MISMATCH = "POSITION_SYMBOL_MISMATCH"


class MarketType(StrEnum):
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    FUTURES = "FUTURES"
    UNKNOWN = "UNKNOWN"


class PositionDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    UNKNOWN = "UNKNOWN"


class InstrumentClass(StrEnum):
    SPOT = "SPOT"
    FUTURES = "FUTURES"
    UNKNOWN = "UNKNOWN"


class PortfolioKnowledgeStatus(StrEnum):
    KNOWN_EMPTY = "KNOWN_EMPTY"
    KNOWN_OPEN = "KNOWN_OPEN"
    UNKNOWN = "UNKNOWN"
    INCONSISTENT = "INCONSISTENT"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RiskMarketContext:
    market_type: MarketType | None
    position_direction: PositionDirection | None
    margin_enabled: bool | None
    leverage: Decimal | None
    instrument_class: InstrumentClass | None
    environment: str | None

    def canonical_value(self) -> dict[str, object]:
        leverage = self.leverage
        return {
            "environment": self.environment,
            "instrument_class": None
            if self.instrument_class is None
            else self.instrument_class.value,
            "leverage": None if leverage is None else str(leverage),
            "margin_enabled": self.margin_enabled,
            "market_type": None if self.market_type is None else self.market_type.value,
            "position_direction": None
            if self.position_direction is None
            else self.position_direction.value,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())

    @property
    def leverage_is_finite(self) -> bool:
        return self.leverage is not None and self.leverage.is_finite()


@dataclass(frozen=True, slots=True)
class OpenPosition:
    position_id: PositionId
    symbol: str
    side: PositionSide

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValidationError("Open position symbol must be non-empty and trimmed")

    def canonical_value(self) -> dict[str, str]:
        return {
            "position_id": str(self.position_id),
            "side": self.side.value,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class PortfolioState:
    knowledge_status: PortfolioKnowledgeStatus
    positions: tuple[OpenPosition, ...]

    @classmethod
    def create(
        cls,
        knowledge_status: PortfolioKnowledgeStatus,
        positions: tuple[OpenPosition, ...] = (),
    ) -> PortfolioState:
        return cls(
            knowledge_status=knowledge_status,
            positions=tuple(sorted(positions, key=lambda position: str(position.position_id))),
        )

    def canonical_value(self) -> dict[str, object]:
        return {
            "knowledge_status": self.knowledge_status.value,
            "positions": [
                position.canonical_value()
                for position in sorted(
                    self.positions,
                    key=lambda position: str(position.position_id),
                )
            ],
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class RiskProvenance:
    strategy_evaluation_id: StrategyEvaluationId | None
    strategy_evaluation_identity: ContentIdentity | None
    strategy_decision_id: StrategyDecisionId | None
    strategy_signal_identity: ContentIdentity | None
    strategy_id: StrategyId | None
    strategy_version: str | None
    strategy_provenance_identity: ContentIdentity | None
    risk_policy_id: RiskPolicyId
    risk_policy_version: str
    risk_policy_identity: ContentIdentity
    environment: str | None
    market_context: RiskMarketContext | None
    market_context_identity: ContentIdentity | None
    portfolio_state_identity: ContentIdentity | None

    def canonical_value(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "market_context": None
            if self.market_context is None
            else self.market_context.canonical_value(),
            "market_context_identity": _optional_identity(self.market_context_identity),
            "portfolio_state_identity": _optional_identity(self.portfolio_state_identity),
            "risk_policy_id": str(self.risk_policy_id),
            "risk_policy_identity": str(self.risk_policy_identity),
            "risk_policy_version": self.risk_policy_version,
            "strategy_decision_id": _optional_identifier(self.strategy_decision_id),
            "strategy_evaluation_id": _optional_identifier(self.strategy_evaluation_id),
            "strategy_evaluation_identity": _optional_identity(self.strategy_evaluation_identity),
            "strategy_id": _optional_identifier(self.strategy_id),
            "strategy_provenance_identity": _optional_identity(self.strategy_provenance_identity),
            "strategy_signal_identity": _optional_identity(self.strategy_signal_identity),
            "strategy_version": self.strategy_version,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class RiskDecision:
    risk_decision_id: RiskDecisionId
    status: RiskStatus
    reason_code: RiskReasonCode
    provenance: RiskProvenance
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        status: RiskStatus,
        reason_code: RiskReasonCode,
        provenance: RiskProvenance,
    ) -> RiskDecision:
        if status is RiskStatus.NO_DECISION:
            raise ValidationError("NO_DECISION is not an economic Risk decision")
        identity = _decision_identity(status, reason_code, provenance)
        return cls(
            risk_decision_id=RiskDecisionId(f"risk-decision:{identity}"),
            status=status,
            reason_code=reason_code,
            provenance=provenance,
            content_identity=identity,
        )

    def __post_init__(self) -> None:
        if self.status is RiskStatus.NO_DECISION:
            raise ValidationError("NO_DECISION cannot be represented by RiskDecision")
        if not _reason_matches_status(self.status, self.reason_code):
            raise ValidationError("Risk decision reason_code is inconsistent with its status")
        expected = _decision_identity(self.status, self.reason_code, self.provenance)
        if self.content_identity != expected:
            raise ValidationError("Risk decision content_identity is inconsistent")
        if self.risk_decision_id != RiskDecisionId(f"risk-decision:{expected}"):
            raise ValidationError("Risk decision identity is inconsistent")


@dataclass(frozen=True, slots=True)
class RiskProcessingResult:
    status: RiskStatus
    reason_code: RiskReasonCode
    provenance: RiskProvenance
    decision: RiskDecision | None
    content_identity: ContentIdentity

    @property
    def risk_decision_id(self) -> RiskDecisionId | None:
        return None if self.decision is None else self.decision.risk_decision_id

    def __post_init__(self) -> None:
        if not _reason_matches_status(self.status, self.reason_code):
            raise ValidationError("Risk result reason_code is inconsistent with its status")
        if self.status is RiskStatus.NO_DECISION:
            if self.decision is not None:
                raise ValidationError("NO_DECISION cannot contain an economic decision")
        elif (
            self.decision is None
            or self.decision.status is not self.status
            or self.decision.reason_code is not self.reason_code
            or self.decision.provenance != self.provenance
        ):
            raise ValidationError("Economic Risk result requires its matching decision")
        expected = ContentIdentity.from_canonical(
            {
                "decision_identity": None
                if self.decision is None
                else str(self.decision.content_identity),
                "provenance": self.provenance.canonical_value(),
                "reason_code": self.reason_code.value,
                "status": self.status.value,
            }
        )
        if self.content_identity != expected:
            raise ValidationError("Risk processing result content_identity is inconsistent")


def _decision_identity(
    status: RiskStatus,
    reason_code: RiskReasonCode,
    provenance: RiskProvenance,
) -> ContentIdentity:
    return ContentIdentity.from_canonical(
        {
            "provenance": provenance.canonical_value(),
            "reason_code": reason_code.value,
            "status": status.value,
        }
    )


def _optional_identity(value: ContentIdentity | None) -> str | None:
    return None if value is None else str(value)


def _optional_identifier(value: object | None) -> str | None:
    return None if value is None else str(value)


def _reason_matches_status(status: RiskStatus, reason_code: RiskReasonCode) -> bool:
    if status is RiskStatus.APPROVED:
        return reason_code is RiskReasonCode.POLICY_COMPLIANT
    if status is RiskStatus.NO_DECISION:
        return reason_code is RiskReasonCode.STRATEGY_NO_ACTION
    if status is RiskStatus.REJECTED:
        return reason_code in {
            RiskReasonCode.MARKET_TYPE_NOT_SPOT,
            RiskReasonCode.POSITION_DIRECTION_NOT_LONG,
            RiskReasonCode.MARGIN_NOT_ALLOWED,
            RiskReasonCode.LEVERAGE_NOT_ALLOWED,
            RiskReasonCode.INSTRUMENT_CLASS_NOT_SPOT,
            RiskReasonCode.MAX_POSITIONS_REACHED,
            RiskReasonCode.NO_OPEN_POSITION,
            RiskReasonCode.POSITION_SYMBOL_MISMATCH,
        }
    return reason_code in {
        RiskReasonCode.STRATEGY_INPUT_INCOMPLETE,
        RiskReasonCode.STRATEGY_INPUT_NOT_REPRODUCIBLE,
        RiskReasonCode.STRATEGY_CONTEXT_INCOMPATIBLE,
        RiskReasonCode.UNKNOWN_ENVIRONMENT,
        RiskReasonCode.ENVIRONMENT_NOT_ACTIVE,
        RiskReasonCode.MARKET_CONTEXT_INCOMPLETE,
        RiskReasonCode.PORTFOLIO_STATE_UNKNOWN,
        RiskReasonCode.PORTFOLIO_STATE_INCONSISTENT,
        RiskReasonCode.PORTFOLIO_POLICY_VIOLATION,
    }
