"""Manual one-attempt orchestration. No default transport or authority is installed."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from atp.exchange.filters import NotionalPriceEvidence, SymbolFilterEvidence
from atp.exchange.model import ExchangeOrderRequest, Status, valid
from atp.exchange.model import Reason as ExchangeReason
from atp.exchange.read_only import EvidenceRecord, encoded, verify_record
from atp.exchange.shadow import inspect_exchange_reply
from atp.exchange.time_evidence import ExchangeTimeEvidence
from atp.exchange.transport import TransportReply
from atp.first_testnet_order.gate import (
    FirstOrderInputs,
    GateClock,
    GateTimeSample,
    evaluate_first_order,
)
from atp.first_testnet_order.ledger import (
    AlreadyConsumed,
    LedgerUnavailable,
    TestnetSubmissionLedger,
)
from atp.first_testnet_order.model import (
    FirstOrderResult,
    FirstTestnetOrderAuthorization,
    Reason,
    SubmissionState,
)
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.testnet_activation.contracts import RuntimeAuthorizationContext


class GateClosed(RuntimeError):
    pass


TESTNET_ENDPOINT = "https://testnet.binance.vision"


@dataclass(frozen=True, slots=True)
class SubmissionPermit(EvidenceRecord):
    authorization_identity: ContentIdentity
    request_identity: ContentIdentity
    credential_source_identity: ContentIdentity
    readiness_identity: ContentIdentity
    at: datetime
    authorization_valid_until: datetime
    price_fresh_until: datetime
    filter_fresh_until: datetime


def submission_permit_time(permit: object, clock: object) -> datetime | None:
    """Fresh trusted time at the economic boundary, with exclusive deadlines."""
    if not verify_record(permit, SubmissionPermit) or not isinstance(clock, GateClock):
        return None
    assert isinstance(permit, SubmissionPermit)
    sample = clock.read()
    if (
        type(sample) is not GateTimeSample
        or not verify_record(sample.evidence, ExchangeTimeEvidence)
        or type(sample.gate_evaluation_time) is not datetime
    ):
        return None
    now = sample.gate_evaluation_time
    evidence = sample.evidence
    if (
        now.tzinfo is not UTC
        or now < permit.at
        or evidence.local_observed_at > now
        or abs(evidence.server_time - evidence.local_observed_at) > timedelta(seconds=5)
        or abs(now - evidence.server_time) > timedelta(seconds=5)
        or not all(
            now < deadline
            for deadline in (
                permit.authorization_valid_until,
                permit.price_fresh_until,
                permit.filter_fresh_until,
            )
        )
    ):
        return None
    return now


_permits: dict[int, tuple[SubmissionPermit, ContentIdentity]] = {}
_permit_lock = Lock()


def consume_submission_permit(
    permit: object,
    order: object,
    at: object,
    credential_source_identity: object,
    readiness_identity: object,
    clock: object = None,
) -> bool:
    """Transport defense: exact process-local, one-use receipt, never caller booleans."""
    with _permit_lock:
        known = _permits.pop(id(permit), None)
    return (
        known is not None
        and known[0] is permit
        and verify_record(permit, SubmissionPermit)
        and isinstance(permit, SubmissionPermit)
        and permit.content_identity == known[1]
        and valid(order, ExchangeOrderRequest)
        and isinstance(order, ExchangeOrderRequest)
        and permit.request_identity == order.content_identity
        and permit.at == at
        and permit.credential_source_identity == credential_source_identity
        and permit.readiness_identity == readiness_identity
        and order.environment == "TESTNET"
        and submission_permit_time(permit, clock) is not None
    )


class FirstOrderTransport(ABC):
    """Trusted composition dependency; implementations must enforce the one-use permit."""

    @property
    @abstractmethod
    def endpoint(self) -> str: ...

    @property
    @abstractmethod
    def credential_source_identity(self) -> ContentIdentity: ...

    @abstractmethod
    def submit(
        self, permit: SubmissionPermit, order: ExchangeOrderRequest, at: datetime, readiness: object
    ) -> TransportReply: ...


def run_first_order(
    inputs: object = None,
    *,
    clock: object = None,
    ledger: object = None,
    transport: object = None,
    execute: bool = False,
    policy: object = None,
) -> FirstOrderResult:
    if type(execute) is not bool:
        return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY)
    result, order, at = evaluate_first_order(inputs, clock, policy)
    if order is None or at is None:
        return result
    assert isinstance(inputs, FirstOrderInputs)
    auth, ctx = inputs.authorization, inputs.runtime_authorization
    assert isinstance(auth, FirstTestnetOrderAuthorization)
    assert isinstance(ctx, RuntimeAuthorizationContext)
    if not isinstance(ledger, TestnetSubmissionLedger):
        return FirstOrderResult(Reason.SUBMISSION_LEDGER_UNAVAILABLE)
    if ledger.content_identity != auth.submission_ledger_identity:
        return FirstOrderResult(Reason.SUBMISSION_LEDGER_UNAVAILABLE)
    if not isinstance(transport, FirstOrderTransport):
        return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY)
    if transport.endpoint != TESTNET_ENDPOINT:
        return FirstOrderResult(
            Reason.LIVE_FORBIDDEN
            if "binance" in transport.endpoint
            else Reason.TESTNET_ENDPOINT_MISMATCH
        )
    if transport.credential_source_identity != ctx.credential_source_identity:
        return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY)
    try:
        if ledger.consumed(auth.content_identity, auth.client_order_id):
            return FirstOrderResult(Reason.FIRST_ORDER_ALREADY_CONSUMED)
        if not execute:
            return result

        def last_gate() -> None:
            nonlocal result, order, at
            result, order, at = evaluate_first_order(inputs, clock, policy)
            if order is None or at is None:
                raise GateClosed()

        ledger.append(
            auth.content_identity,
            auth.client_order_id,
            SubmissionState.ATTEMPT_STARTED,
            before_reservation=last_gate,
        )
    except GateClosed:
        return result
    except AlreadyConsumed:
        return FirstOrderResult(Reason.FIRST_ORDER_ALREADY_CONSUMED)
    except LedgerUnavailable:
        return FirstOrderResult(Reason.SUBMISSION_LEDGER_UNAVAILABLE)
    from atp.ops.model import OperationalReadinessResult

    assert isinstance(inputs.readiness, OperationalReadinessResult)
    readiness_identity = inputs.readiness.content_identity
    assert isinstance(inputs.price, NotionalPriceEvidence)
    assert isinstance(inputs.filters, SymbolFilterEvidence)
    permit = SubmissionPermit(
        auth.content_identity,
        order.content_identity,
        ctx.credential_source_identity,
        readiness_identity,
        at,
        min(auth.valid_until, ctx.validity_end),
        inputs.price.effective_at + timedelta(seconds=10),
        inputs.filters.observed_at + timedelta(minutes=15),
    )
    with _permit_lock:
        _permits[id(permit)] = (permit, permit.content_identity)
    try:
        try:
            reply = transport.submit(permit, order, at, inputs.readiness)
        except (OSError, TimeoutError):
            # No exception text is retained. Attempt remains consumed even on lost response.
            reply = None
    finally:
        with _permit_lock:
            _permits.pop(id(permit), None)
    calls = (
        0
        if (
            type(reply) is TransportReply
            and reply.error is ExchangeReason.TESTNET_RUNTIME_BLOCKED
            and reply.possibly_sent is False
        )
        else 1
    )
    mapped = inspect_exchange_reply(reply, order, at)
    state = (
        SubmissionState.ACKNOWLEDGED
        if mapped.status is Status.ACCEPTED
        else SubmissionState.UNKNOWN
    )
    reason = (
        Reason.RECONCILIATION_REQUIRED
        if state is SubmissionState.ACKNOWLEDGED
        else Reason.SUBMISSION_STATE_UNKNOWN
    )
    # Raw status is admitted only from a positive, typed mapped response and a closed enum.
    from atp.exchange.read_only import ExchangeOrderStatus

    status = None
    if mapped.status is Status.REJECTED:
        state, reason, status = SubmissionState.RECONCILED, Reason.EXCHANGE_REJECTED, "REJECTED"
    if (
        mapped.status is Status.ACCEPTED
        and type(reply) is TransportReply
        and type(reply.body) is dict
    ):
        raw = reply.body.get("status")
        status = (
            raw if type(raw) is str and raw in ExchangeOrderStatus._value2member_map_ else "UNKNOWN"
        )
    result = FirstOrderResult(
        reason,
        state,
        auth.content_identity,
        order.content_identity,
        mapped.content_identity,
        mapped.exchange_order_id,
        status,
        mapped.exchange_event_time,
        calls,
    )
    try:
        ledger.append(
            auth.content_identity,
            auth.client_order_id,
            state,
            canonical_json_bytes(encoded(result)),
        )
    except LedgerUnavailable:
        return FirstOrderResult(
            Reason.SUBMISSION_STATE_UNKNOWN,
            SubmissionState.UNKNOWN,
            auth.content_identity,
            order.content_identity,
            transport_call_count=calls,
        )
    return result
