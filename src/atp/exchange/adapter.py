"""Synchronous, local idempotence boundary. No economic decisions or implicit retries."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import Lock

from atp.exchange.model import (
    ExchangeConnectivityResult,
    ExchangeOrderRequest,
    ExchangePolicy,
    ExchangeSubmissionResult,
    Reason,
    Side,
    Status,
    TestnetExecutionAuthorization,
    UpstreamOrderProof,
    canonical,
    request_from_proof,
    valid,
)
from atp.exchange.transport import ExchangeTransport, Operation, TransportReply, runtime_ready
from atp.ops.model import OperationalReadinessResult
from atp.risk.model import InstrumentClass, MarketType, PositionDirection, RiskDecision, RiskStatus
from atp.risk.policy import RiskPolicy
from atp.shared.identity import ContentIdentity
from atp.strategy.model import SignalKind, StrategyEvaluation


def _order_error(
    order: object, proof: object, risk: object, strategy: object, risk_environment: str = "TESTNET"
) -> Reason | None:
    if (
        type(order) is ExchangeOrderRequest
        and type(getattr(order, "environment", None)) is str
        and order.environment == "LIVE"
    ):
        return Reason.LIVE_FORBIDDEN
    if type(order) is ExchangeOrderRequest and (
        type(getattr(order, "quantity", None)) is not Decimal
        or not order.quantity.is_finite()
        or order.quantity <= 0
    ):
        return Reason.INVALID_QUANTITY
    if not valid(order, ExchangeOrderRequest) or not valid(proof, UpstreamOrderProof):
        return Reason.INVALID_EXCHANGE_ORDER
    assert isinstance(order, ExchangeOrderRequest) and isinstance(proof, UpstreamOrderProof)
    if order.environment != "TESTNET":
        return Reason.TESTNET_NOT_AUTHORIZED
    if order.order_type != "MARKET":
        return Reason.UNSUPPORTED_ORDER_TYPE
    if not re.fullmatch(r"[A-Z0-9]{2,30}", order.symbol):
        return Reason.SYMBOL_MISMATCH
    if order != request_from_proof(proof):
        return Reason.INVALID_EXCHANGE_ORDER
    if not valid(risk, RiskDecision) or not valid(strategy, StrategyEvaluation):
        return Reason.RISK_AUTHORIZATION_INVALID
    assert isinstance(risk, RiskDecision) and isinstance(strategy, StrategyEvaluation)
    p = risk.provenance
    if not p.risk_policy_version or p.risk_policy_version.strip() != p.risk_policy_version:
        return Reason.RISK_AUTHORIZATION_INVALID
    market = p.market_context
    signal = strategy.signal
    if (
        risk.status is not RiskStatus.APPROVED
        or p.environment != risk_environment
        or p.portfolio_state_identity is None
        or p.risk_policy_identity
        != RiskPolicy.v1(policy_id=p.risk_policy_id, version=p.risk_policy_version).content_identity
        or market is None
        or market.market_type is not MarketType.SPOT
        or market.instrument_class is not InstrumentClass.SPOT
        or market.position_direction is not PositionDirection.LONG
        or market.margin_enabled is not False
        or market.leverage != Decimal(1)
        or market.environment != risk_environment
        or market.symbol != order.symbol
        or p.market_context_identity != market.content_identity
        or strategy.provenance.environment.value != risk_environment
        or strategy.provenance.symbol != order.symbol
        or signal is None
        or signal.kind is not (SignalKind.LONG_ENTRY if order.side is Side.BUY else SignalKind.EXIT)
        or p.strategy_evaluation_id != strategy.strategy_evaluation_id
        or p.strategy_evaluation_identity != strategy.content_identity
        or p.strategy_signal_identity != signal.content_identity
        or p.strategy_decision_id != signal.strategy_decision_id
        or p.strategy_provenance_identity != strategy.provenance.content_identity
        or p.strategy_id != strategy.provenance.strategy_id
        or p.strategy_version != strategy.provenance.strategy_version
        or proof.strategy_evaluation_identity != strategy.content_identity
        or order.risk_decision_identity != risk.content_identity
        or order.risk_decision_id != str(risk.risk_decision_id)
        or order.created_at < strategy.provenance.evaluation_time.value
    ):
        return Reason.RISK_AUTHORIZATION_INVALID
    return None


def authorize_testnet(readiness: object) -> TestnetExecutionAuthorization | None:
    """Runtime factory. Current OPS policy cannot produce this authorization."""
    if not runtime_ready(readiness):
        return None
    assert isinstance(readiness, OperationalReadinessResult)
    if (
        readiness.qualification_result_identity is None
        or readiness.observability_evidence_identity is None
    ):
        return None
    data = dict(
        environment="TESTNET",
        ops_readiness_identity=readiness.content_identity,
        qualification_identity=readiness.qualification_result_identity,
        observability_identity=readiness.observability_evidence_identity,
        issued_for_policy=ExchangePolicy().content_identity,
    )
    return TestnetExecutionAuthorization(
        **data,  # type: ignore[arg-type]
        content_identity=ContentIdentity.from_canonical({k: canonical(v) for k, v in data.items()}),
    )


class ExchangeAdapter:
    def __init__(self, transport: ExchangeTransport, policy: ExchangePolicy | None = None) -> None:
        self._transport = transport
        self._policy = ExchangePolicy() if policy is None else policy
        self._known: dict[str, ExchangeSubmissionResult] = {}
        self._orders: dict[str, ContentIdentity] = {}
        self._lock = Lock()

    def check_connectivity(
        self, *, authorization: object, readiness: object, checked_at: object
    ) -> ExchangeConnectivityResult:
        """A transport check has no order acceptance or readiness authority."""
        reason = Reason.TESTNET_NOT_AUTHORIZED
        if not valid(self._policy, ExchangePolicy):
            reason = Reason.EXCHANGE_POLICY_MISMATCH
        elif not valid(checked_at, datetime):
            reason = Reason.TRANSPORT_UNAVAILABLE
        elif (
            valid(authorization, TestnetExecutionAuthorization)
            and authorize_testnet(readiness) is not None
            and authorization == authorize_testnet(readiness)
        ):
            assert isinstance(checked_at, datetime)
            reply = self._transport.perform(Operation.PING, None, checked_at, readiness)
            reason = Reason.TRANSPORT_UNAVAILABLE
            if type(reply) is TransportReply:
                if reply.error is not None and type(reply.error) is Reason:
                    reason = reply.error
                elif reply.http_status == 200 and type(reply.body) is dict and not reply.body:
                    reason = Reason.EXCHANGE_OK
        return ExchangeConnectivityResult(
            reason is Reason.EXCHANGE_OK, reason, ExchangePolicy().content_identity
        )

    def _authorize(
        self,
        order: object,
        proof: object,
        risk: object,
        strategy: object,
        authorization: object,
        readiness: object,
    ) -> Reason | None:
        error = _order_error(order, proof, risk, strategy)
        if error is not None:
            return error
        if not valid(authorization, TestnetExecutionAuthorization):
            return Reason.TESTNET_NOT_AUTHORIZED
        expected = authorize_testnet(readiness)
        if expected is None or authorization != expected:
            return Reason.TESTNET_NOT_AUTHORIZED
        return None

    def submit(
        self,
        order: object,
        *,
        proof: object,
        risk: object,
        strategy: object,
        authorization: object,
        readiness: object,
        submitted_at: object,
    ) -> ExchangeSubmissionResult:
        return self._process(
            order, proof, risk, strategy, authorization, readiness, submitted_at, False
        )

    def reconcile(
        self,
        order: object,
        *,
        proof: object,
        risk: object,
        strategy: object,
        authorization: object,
        readiness: object,
        submitted_at: object,
    ) -> ExchangeSubmissionResult:
        return self._process(
            order, proof, risk, strategy, authorization, readiness, submitted_at, True
        )

    def _process(
        self,
        order: object,
        proof: object,
        risk: object,
        strategy: object,
        authorization: object,
        readiness: object,
        at: object,
        reconcile: bool,
    ) -> ExchangeSubmissionResult:
        if not valid(self._policy, ExchangePolicy):
            return self._result(Status.BLOCKED, Reason.EXCHANGE_POLICY_MISMATCH)
        error = self._authorize(order, proof, risk, strategy, authorization, readiness)
        if error is not None:
            return self._result(Status.BLOCKED, error)
        if not valid(at, datetime):
            return self._result(Status.BLOCKED, Reason.INVALID_EXCHANGE_ORDER)
        assert isinstance(at, datetime) and isinstance(order, ExchangeOrderRequest)
        if at < order.created_at:
            return self._result(Status.BLOCKED, Reason.INVALID_EXCHANGE_ORDER)
        with self._lock:
            key = order.client_order_id
            if key in self._orders and self._orders[key] != order.content_identity:
                return self._result(Status.BLOCKED, Reason.DUPLICATE_SUBMISSION)
            previous = self._known.get(key)
            if (
                previous is not None
                and valid(previous, ExchangeSubmissionResult)
                and previous.status in (Status.ACCEPTED, Status.REJECTED)
            ):
                return previous
            if reconcile and key not in self._orders:
                return self._result(Status.BLOCKED, Reason.RECONCILIATION_REQUIRED)
            query = reconcile or key in self._orders
            # Reserve before dispatch, even if an internal error interrupts the call.
            self._orders[key] = order.content_identity
            reply = self._transport.perform(
                Operation.QUERY if query else Operation.SUBMIT, order, at, readiness
            )
            result = self._map(reply, order, at, query)
            # A malformed query never erases an uncertain original submission.
            if result.status in (Status.ACCEPTED, Status.REJECTED, Status.UNCERTAIN):
                self._known[key] = result
            return result

    def _map(
        self, reply: object, order: ExchangeOrderRequest, at: datetime, query: bool
    ) -> ExchangeSubmissionResult:
        uncertain = Status.BLOCKED if query else Status.UNCERTAIN
        if type(reply) is not TransportReply:
            return self._result(uncertain, Reason.MALFORMED_EXCHANGE_RESPONSE, order, at)
        if type(reply.possibly_sent) is not bool or (
            reply.error is not None and type(reply.error) is not Reason
        ):
            return self._result(uncertain, Reason.MALFORMED_EXCHANGE_RESPONSE, order, at)
        if reply.error is not None:
            if reply.error is Reason.MALFORMED_EXCHANGE_RESPONSE:
                return self._result(uncertain, reply.error, order, at)
            if reply.possibly_sent or query:
                return self._result(Status.UNCERTAIN, Reason.SUBMISSION_OUTCOME_UNKNOWN, order, at)
            reason = (
                reply.error
                if reply.error
                in (
                    Reason.CREDENTIALS_UNAVAILABLE,
                    Reason.INVALID_CREDENTIALS,
                    Reason.TESTNET_NOT_AUTHORIZED,
                )
                else Reason.TRANSPORT_UNAVAILABLE
            )
            return self._result(Status.BLOCKED, reason, order, at)
        body, http = reply.body, reply.http_status
        if type(body) is not dict or type(http) is not int or not _json_value(body):
            return self._result(uncertain, Reason.MALFORMED_EXCHANGE_RESPONSE, order, at)
        code = body.get("code")
        if http >= 500 or code in (-1006, -1007):
            return self._result(Status.UNCERTAIN, Reason.SUBMISSION_OUTCOME_UNKNOWN, order, at)
        if query and http != 200:
            # Not-found is not proof of non-execution in an asynchronous exchange.
            # V1 deliberately does not use the optional controlled resubmission permission.
            return self._result(Status.UNCERTAIN, Reason.RECONCILIATION_FAILED, order, at)
        if 400 <= http < 500 and type(code) is int and code < 0:
            return self._result(Status.REJECTED, Reason.EXCHANGE_REJECTED, order, at)
        if http != 200:
            return self._result(uncertain, Reason.MALFORMED_EXCHANGE_RESPONSE, order, at)
        timestamp = body.get("updateTime") if query else body.get("transactTime")
        quantity = body.get("origQty")
        try:
            quantity_matches = (
                type(quantity) is str
                and Decimal(quantity).is_finite()
                and Decimal(quantity) == order.quantity
            )
        except InvalidOperation:
            quantity_matches = False
        if (
            body.get("symbol") != order.symbol
            or body.get("clientOrderId") != order.client_order_id
            or body.get("side") != order.side.value
            or body.get("type") != "MARKET"
            or type(body.get("orderId")) is not int
            or body["orderId"] < 0
            or type(timestamp) is not int
            or timestamp < 0
            or not quantity_matches
            or body.get("status")
            not in ("NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH")
        ):
            return self._result(uncertain, Reason.MALFORMED_EXCHANGE_RESPONSE, order, at)
        try:
            event_time = datetime.fromtimestamp(timestamp / 1000, UTC)
        except (ValueError, OverflowError, OSError):
            return self._result(uncertain, Reason.MALFORMED_EXCHANGE_RESPONSE, order, at)
        return self._result(
            Status.ACCEPTED, Reason.EXCHANGE_OK, order, at, str(body["orderId"]), event_time
        )

    @staticmethod
    def _result(
        status: Status,
        reason: Reason,
        order: ExchangeOrderRequest | None = None,
        at: datetime | None = None,
        exchange_id: str | None = None,
        event_time: datetime | None = None,
    ) -> ExchangeSubmissionResult:
        data = dict(
            status=status,
            reason_code=reason,
            venue="BINANCE",
            environment="TESTNET",
            client_order_id=None if order is None else order.client_order_id,
            exchange_order_id=exchange_id,
            symbol=None if order is None else order.symbol,
            side=None if order is None else order.side,
            submitted_at=at,
            exchange_event_time=event_time,
            request_identity=None if order is None else order.content_identity,
            upstream_order_identity=None if order is None else order.upstream_order_identity,
            risk_decision_identity=None if order is None else order.risk_decision_identity,
            policy_identity=ExchangePolicy().content_identity,
        )
        return ExchangeSubmissionResult(
            **data,  # type: ignore[arg-type]
            content_identity=ContentIdentity.from_canonical(
                {k: canonical(v) for k, v in data.items()}
            ),
        )


def _json_value(value: object, depth: int = 0) -> bool:
    if depth > 20:
        return False
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is list:
        return all(_json_value(v, depth + 1) for v in value)
    if type(value) is dict:
        return all(type(k) is str and _json_value(v, depth + 1) for k, v in value.items())
    return False
