"""Single read-only submission authorization gate. V1 never permits transport."""

from datetime import datetime

from atp.exchange.adapter import _order_error
from atp.exchange.filters import check_market_filters
from atp.exchange.model import ExchangeOrderRequest
from atp.ops.inspection import validate_readiness_result
from atp.ops.model import OperationalReadinessResult, ReadinessStatus
from atp.release_deployment.engine import promote
from atp.release_deployment.model import PromotionStatus, Target
from atp.risk.model import RiskDecision
from atp.shared.environment import Environment
from atp.testnet_activation.contracts import (
    ActivationResult,
    Reason,
    RuntimeAuthorizationContext,
    TestnetActivationGrant,
    context_error,
    valid,
)


def authorize_exchange_submission(
    order: object,
    *,
    proof: object = None,
    risk: object = None,
    strategy: object = None,
    grant: object = None,
    runtime_authorization: object = None,
    readiness: object = None,
    release: object = None,
    wheel: object = None,
    promotion: object = None,
    filters: object = None,
    price: object = None,
    at: object = None,
) -> ActivationResult:
    def block(reason: Reason) -> ActivationResult:
        return ActivationResult(reason)

    if type(order) is ExchangeOrderRequest and getattr(order, "environment", None) == "LIVE":
        return block(Reason.LIVE_FORBIDDEN)
    error = context_error(runtime_authorization, at)
    if error:
        return block(error)
    assert isinstance(runtime_authorization, RuntimeAuthorizationContext)
    if not valid(grant, TestnetActivationGrant):
        return block(Reason.ACTIVATION_GRANT_INVALID)
    assert isinstance(grant, TestnetActivationGrant)
    if grant.content_identity != runtime_authorization.activation_grant_identity:
        return block(Reason.ACTIVATION_GRANT_UNTRUSTED)
    if _order_error(order, proof, risk, strategy) is not None:
        return block(Reason.RISK_NOT_AUTHORIZED)
    assert isinstance(order, ExchangeOrderRequest) and isinstance(risk, RiskDecision)
    error = context_error(
        runtime_authorization, at, symbol=order.symbol, order_type=order.order_type
    )
    if error:
        return block(error)
    assert isinstance(at, datetime)
    if order.created_at > at:
        return block(Reason.INVALID_ACTIVATION_INPUT)
    if risk.provenance.runtime_authorization_identity != runtime_authorization.content_identity:
        return block(Reason.RISK_NOT_AUTHORIZED)
    if (
        not validate_readiness_result(readiness)
        or not isinstance(readiness, OperationalReadinessResult)
        or readiness.readiness_status is not ReadinessStatus.READY
        or readiness.environment is not Environment.TESTNET
        or readiness.runtime_authorization_identity != runtime_authorization.content_identity
    ):
        return block(Reason.OPS_NOT_READY)
    expected = promote(
        release, wheel, Target.TESTNET, runtime_authorization=runtime_authorization, at=at
    )
    if expected.status is not PromotionStatus.ALLOWED or promotion != expected:
        return block(Reason.RELEASE_NOT_PROMOTED)
    if check_market_filters(filters, order.symbol, order.quantity, at, price) is not None:
        return block(Reason.INVALID_ACTIVATION_INPUT)
    # Normative constant, not a configuration flag. ENG-TO-001 is a separate CTO gate.
    return block(Reason.TESTNET_RUNTIME_BLOCKED)
