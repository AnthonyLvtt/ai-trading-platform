"""Public projection contract. Never returns a runtime execution authorization."""

from dataclasses import dataclass
from decimal import Decimal

from atp.exchange.adapter import _order_error
from atp.exchange.model import (
    ExchangePolicy,
    ExchangeSubmissionResult,
    UpstreamOrderProof,
    decimal_text,
    request_from_proof,
    valid,
)
from atp.exchange.read_only import EvidenceRecord, verify_record
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class ExchangeShadowProjection(EvidenceRecord):
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    client_order_id: str
    upstream_identity: ContentIdentity
    adapter_policy_identity: ContentIdentity
    source_risk_environment: str = "TEST"
    qualification_target: str = "TESTNET"
    submission_authorized: bool = False

    def parameters(self) -> tuple[tuple[str, str], ...]:
        return (
            ("symbol", self.symbol),
            ("side", self.side),
            ("type", self.order_type),
            ("quantity", decimal_text(self.quantity)),
            ("newClientOrderId", self.client_order_id),
        )


def project_test_shadow(
    proof: object, risk: object, strategy: object
) -> ExchangeShadowProjection | None:
    if not valid(proof, UpstreamOrderProof):
        return None
    assert isinstance(proof, UpstreamOrderProof)
    request = request_from_proof(proof)
    if _order_error(request, proof, risk, strategy, risk_environment="TEST") is not None:
        return None
    return ExchangeShadowProjection(
        request.symbol,
        request.side.value,
        request.order_type,
        request.quantity,
        request.client_order_id,
        proof.content_identity,
        ExchangePolicy().content_identity,
    )


def inspect_exchange_reply(
    reply: object, order: object, at: object, *, query: bool = False
) -> ExchangeSubmissionResult:
    """Parse only: this public facade cannot submit, reconcile or retry an order."""
    from datetime import datetime

    from atp.exchange.adapter import ExchangeAdapter
    from atp.exchange.model import ExchangeOrderRequest, Reason, Status

    if (
        not verify_record(order, ExchangeOrderRequest)
        or not valid(at, datetime)
        or type(query) is not bool
    ):
        return ExchangeAdapter._result(Status.BLOCKED, Reason.MALFORMED_EXCHANGE_RESPONSE)
    assert isinstance(order, ExchangeOrderRequest) and isinstance(at, datetime)
    return ExchangeAdapter._map(reply, order, at, query)
