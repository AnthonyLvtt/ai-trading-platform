"""Read-only final preparation. Fresh time is injected at every evaluation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import localcontext

from atp.exchange.filters import (
    NotionalPriceEvidence,
    SymbolFilterEvidence,
    check_order_capacity,
    market_notional_price_contract,
)
from atp.exchange.model import (
    ExchangeOrderRequest,
    Side,
    UpstreamOrderProof,
    canonical,
    request_from_proof,
    valid,
)
from atp.exchange.read_only import verify_record
from atp.exchange.submission_gate import inspect_submission_prerequisites
from atp.exchange.time_evidence import ExchangeTimeEvidence
from atp.first_testnet_order.model import (
    FirstOrderPolicy,
    FirstOrderResult,
    FirstTestnetOrderAuthorization,
    Reason,
)
from atp.first_testnet_order.trust import receipt_valid
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.contracts import (
    Reason as ActivationReason,
)
from atp.testnet_activation.contracts import (
    RuntimeAuthorizationContext,
    context_error,
)
from atp.testnet_activation.contracts import (
    valid as activation_valid,
)


@dataclass(frozen=True, slots=True)
class GateTimeSample:
    """Evaluation time is ephemeral, outside normative proof identities."""

    gate_evaluation_time: datetime
    evidence: ExchangeTimeEvidence


class GateClock(ABC):
    @abstractmethod
    def read(self) -> GateTimeSample: ...


class ServerTimeSource(ABC):
    @abstractmethod
    def read(self) -> ExchangeTimeEvidence: ...


class ProcessGateClock(GateClock):
    """Trusted composition injects the server-time source. No default network source."""

    def __init__(self, source: ServerTimeSource) -> None:
        self.source = source

    def read(self) -> GateTimeSample:
        evidence = self.source.read()
        return GateTimeSample(datetime.now(UTC), evidence)


@dataclass(frozen=True, slots=True)
class FirstOrderInputs:
    authorization: object = None
    receipt: object = None
    proof: object = None
    risk: object = None
    strategy: object = None
    grant: object = None
    runtime_authorization: object = None
    readiness: object = None
    release: object = None
    wheel: object = None
    promotion: object = None
    filters: object = None
    price: object = None
    open_orders: object = None


def evaluate_first_order(
    inputs: object, clock: object, policy: object = None
) -> tuple[FirstOrderResult, ExchangeOrderRequest | None, datetime | None]:
    def blocked(reason: Reason) -> tuple[FirstOrderResult, None, None]:
        return FirstOrderResult(reason), None, None

    policy = FirstOrderPolicy() if policy is None else policy
    if not verify_record(policy, FirstOrderPolicy) or type(inputs) is not FirstOrderInputs:
        return blocked(Reason.FIRST_ORDER_NOT_READY)
    assert isinstance(inputs, FirstOrderInputs)
    auth = inputs.authorization
    if auth is None:
        return blocked(Reason.FIRST_ORDER_AUTHORIZATION_REQUIRED)
    if (
        type(auth) is FirstTestnetOrderAuthorization
        and getattr(auth, "environment", None) == "LIVE"
    ):
        return blocked(Reason.LIVE_FORBIDDEN)
    if not verify_record(auth, FirstTestnetOrderAuthorization):
        return blocked(Reason.FIRST_ORDER_AUTHORIZATION_INVALID)
    assert isinstance(auth, FirstTestnetOrderAuthorization)
    if not receipt_valid(inputs.receipt, auth):
        return blocked(Reason.FIRST_ORDER_AUTHORIZATION_UNTRUSTED)
    ctx = inputs.runtime_authorization
    if not activation_valid(ctx, RuntimeAuthorizationContext):
        return blocked(Reason.FIRST_ORDER_NOT_READY)
    assert isinstance(ctx, RuntimeAuthorizationContext)
    if (
        auth.activation_grant_identity != ctx.activation_grant_identity
        or auth.runtime_authorization_context_identity != ctx.content_identity
        or auth.source_commit_sha != ctx.source_commit_sha
        or auth.release_identity != ctx.release_identity
        or auth.tq_identity != ctx.tq_identity
    ):
        return blocked(Reason.FIRST_ORDER_AUTHORIZATION_MISMATCH)
    proof = inputs.proof
    if not valid(proof, UpstreamOrderProof):
        return blocked(Reason.QUANTITY_NOT_AUTHORIZED)
    assert isinstance(proof, UpstreamOrderProof)
    if proof.environment == "LIVE":
        return blocked(Reason.LIVE_FORBIDDEN)
    if auth.quantity != proof.quantity or auth.upstream_proof_identity != proof.content_identity:
        return blocked(Reason.QUANTITY_NOT_AUTHORIZED)
    if auth.symbol != proof.symbol or proof.side is not Side.BUY:
        return blocked(Reason.FIRST_ORDER_AUTHORIZATION_MISMATCH)
    error, at = _freshness(auth, inputs.filters, inputs.price, clock)
    if error is not None:
        return blocked(error)
    capacity_error = check_order_capacity(inputs.filters, inputs.open_orders, auth.symbol, at)
    if capacity_error is not None:
        return blocked(Reason(capacity_error))
    ordinary = request_from_proof(proof)
    evidence = {
        f.name: getattr(inputs, f.name)
        for f in fields(inputs)
        if f.name not in ("authorization", "receipt")
    }
    result = inspect_submission_prerequisites(ordinary, **evidence, at=at)
    if result.reason_code is not ActivationReason.TESTNET_ACTIVATION_ALLOWED:
        return blocked(
            Reason.LIVE_FORBIDDEN
            if result.reason_code is ActivationReason.LIVE_FORBIDDEN
            else Reason.FIRST_ORDER_NOT_READY
        )
    values = {
        f.name: getattr(ordinary, f.name) for f in fields(ordinary) if f.name != "content_identity"
    }
    values["client_order_id"] = auth.client_order_id
    identity = ContentIdentity.from_canonical({k: canonical(v) for k, v in values.items()})
    order = ExchangeOrderRequest(**values, content_identity=identity)
    # Sample after potentially expensive release/qualification inspection and request hashing.
    error, at = _freshness(auth, inputs.filters, inputs.price, clock)
    if error is not None:
        return blocked(error)
    capacity_error = check_order_capacity(inputs.filters, inputs.open_orders, auth.symbol, at)
    if capacity_error is not None:
        return blocked(Reason(capacity_error))
    if context_error(ctx, at) is not None:
        return blocked(Reason.FIRST_ORDER_NOT_READY)
    return (
        FirstOrderResult(
            Reason.READY_TO_SUBMIT,
            authorization_identity=auth.content_identity,
            request_identity=order.content_identity,
        ),
        order,
        at,
    )


def _freshness(
    auth: FirstTestnetOrderAuthorization, filters: object, price: object, clock: object
) -> tuple[Reason | None, datetime | None]:
    if not isinstance(clock, GateClock):
        return Reason.TIME_EVIDENCE_INVALID, None
    time = clock.read()
    if (
        type(time) is not GateTimeSample
        or not verify_record(time.evidence, ExchangeTimeEvidence)
        or type(time.gate_evaluation_time) is not datetime
    ):
        return Reason.TIME_EVIDENCE_INVALID, None
    at = time.gate_evaluation_time
    if (
        at.tzinfo is not UTC
        or time.evidence.local_observed_at > at
        or abs(time.evidence.server_time - time.evidence.local_observed_at) > timedelta(seconds=5)
        or time.evidence.server_time.tzinfo is not UTC
        or abs(at - time.evidence.server_time) > timedelta(seconds=5)
    ):
        return Reason.TIME_EVIDENCE_INVALID, None
    if at < auth.valid_from:
        return Reason.FIRST_ORDER_NOT_READY, None
    if at >= auth.valid_until:
        return Reason.FIRST_ORDER_AUTHORIZATION_EXPIRED, None
    if not verify_record(filters, SymbolFilterEvidence):
        return Reason.INVALID_FILTER_EVIDENCE, None
    assert isinstance(filters, SymbolFilterEvidence)
    if filters.observed_at > at:
        return Reason.INVALID_FILTER_EVIDENCE, None
    if at - filters.observed_at > timedelta(minutes=15):
        return Reason.SYMBOL_FILTER_EVIDENCE_STALE, None
    if not verify_record(price, NotionalPriceEvidence):
        return Reason.FIRST_ORDER_NOT_READY, None
    assert isinstance(price, NotionalPriceEvidence)
    if not price.effective_at <= price.observed_at <= at:
        return Reason.TIME_EVIDENCE_INVALID, None
    if at - price.effective_at > timedelta(seconds=10):
        return Reason.PRICE_EVIDENCE_STALE, None
    if (
        price.symbol != auth.symbol
        or price.price <= 0
        or market_notional_price_contract(filters) != (price.source_type, price.avg_price_minutes)
    ):
        return Reason.FIRST_ORDER_NOT_READY, None
    # Exact multiplication independent of the caller's Decimal context.
    with localcontext() as context:
        context.prec = len(auth.quantity.as_tuple().digits) + len(price.price.as_tuple().digits) + 1
        projected = auth.quantity * price.price
    if projected > auth.max_quote_notional:
        return Reason.NOTIONAL_LIMIT_EXCEEDED, None
    return None, at
