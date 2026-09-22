"""Read-only execution evidence. Deliberately no Accounting import or mutation."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from atp.exchange.read_only import (
    EvidenceRecord,
    ExchangeFill,
    ExchangeOrderStatus,
    ReconciliationLookup,
    decimal_field,
    encoded,
    parse_reconciliation,
    verify_record,
)
from atp.first_testnet_order.ledger import LedgerUnavailable, TestnetSubmissionLedger
from atp.first_testnet_order.model import (
    FirstOrderResult,
    FirstTestnetOrderAuthorization,
    Reason,
    SubmissionState,
)
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class ReconciledExchangeExecutionEvidence(EvidenceRecord):
    symbol: str
    side: str
    exchange_order_id: int
    client_order_id: str
    exchange_status: ExchangeOrderStatus
    fills: tuple[ExchangeFill, ...]
    cumulative_base_quantity: Decimal
    cumulative_quote_quantity: Decimal
    reconciliation_source_identity: ContentIdentity
    reconciliation_time: datetime
    authorization_identity: ContentIdentity
    submission_ledger_identity: ContentIdentity
    reservation_identity: ContentIdentity
    environment: str = "TESTNET"


def reconcile_first_order(
    authorization: object, ledger: object, order_payload: object, trades_payload: object, at: object
) -> tuple[FirstOrderResult, ReconciledExchangeExecutionEvidence | None]:
    if not verify_record(authorization, FirstTestnetOrderAuthorization) or not isinstance(
        ledger, TestnetSubmissionLedger
    ):
        return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY), None
    assert isinstance(authorization, FirstTestnetOrderAuthorization)
    auth = authorization
    if ledger.content_identity != auth.submission_ledger_identity:
        return FirstOrderResult(Reason.SUBMISSION_LEDGER_UNAVAILABLE), None
    try:
        reservation = ledger.reservation_identity(auth.content_identity, auth.client_order_id)
        if not ledger.consumed(auth.content_identity, auth.client_order_id):
            return FirstOrderResult(Reason.FIRST_ORDER_NOT_READY), None
        result = parse_reconciliation(
            ReconciliationLookup(auth.symbol, auth.client_order_id), order_payload, trades_payload
        )
        snapshot = result.snapshot
        established_id = ledger.established_order_id(auth.content_identity, auth.client_order_id)
        if (
            result.status != "PASSED"
            or snapshot is None
            or type(at) is not datetime
            or (established_id is not None and str(snapshot.exchange_order_id) != established_id)
            or snapshot.side != auth.side
            or snapshot.order_type != auth.order_type
            or snapshot.quantity != auth.quantity
            or at.tzinfo is not auth.valid_from.tzinfo
            or snapshot.effective_at > at
            or at < auth.valid_from
            or any(f.execution_time < auth.valid_from for f in snapshot.fills)
            or any(f.quote_quantity is None for f in snapshot.fills)
        ):
            return FirstOrderResult(
                Reason.SUBMISSION_STATE_UNKNOWN, SubmissionState.UNKNOWN, auth.content_identity
            ), None
        # Require complete fills before reporting cumulative execution facts.
        amounts = [
            v for f in snapshot.fills for v in (f.quantity, f.quote_quantity) if v is not None
        ]
        with localcontext() as context:
            context.prec = max(
                28,
                sum(len(v.as_tuple().digits) + abs(int(v.as_tuple().exponent)) for v in amounts)
                + 10,
            )
            base = sum((f.quantity for f in snapshot.fills), Decimal(0))
            quote = sum(
                (f.quote_quantity for f in snapshot.fills if f.quote_quantity is not None),
                Decimal(0),
            )
        if isinstance(order_payload, dict) and "cummulativeQuoteQty" in order_payload:
            try:
                if decimal_field(order_payload["cummulativeQuoteQty"]) != quote:
                    return FirstOrderResult(
                        Reason.SUBMISSION_STATE_UNKNOWN,
                        SubmissionState.UNKNOWN,
                        auth.content_identity,
                    ), None
            except ValueError:
                return FirstOrderResult(
                    Reason.SUBMISSION_STATE_UNKNOWN, SubmissionState.UNKNOWN, auth.content_identity
                ), None
        if (
            base != snapshot.executed_quantity
            or (snapshot.status is ExchangeOrderStatus.FILLED and base != auth.quantity)
            or (
                snapshot.status is ExchangeOrderStatus.PARTIALLY_FILLED
                and not 0 < base < auth.quantity
            )
        ):
            return FirstOrderResult(
                Reason.SUBMISSION_STATE_UNKNOWN, SubmissionState.UNKNOWN, auth.content_identity
            ), None
        evidence = ReconciledExchangeExecutionEvidence(
            auth.symbol,
            auth.side,
            snapshot.exchange_order_id,
            auth.client_order_id,
            snapshot.status,
            snapshot.fills,
            base,
            quote,
            snapshot.source_identity,
            at,
            auth.content_identity,
            ledger.content_identity,
            reservation,
        )
        ledger.append(
            auth.content_identity,
            auth.client_order_id,
            SubmissionState.RECONCILED,
            canonical_json_bytes(encoded(evidence)),
        )
        return FirstOrderResult(
            Reason.RECONCILED,
            SubmissionState.RECONCILED,
            auth.content_identity,
            response_identity=evidence.content_identity,
            exchange_order_id=str(snapshot.exchange_order_id),
            exchange_status=snapshot.status.value,
        ), evidence
    except LedgerUnavailable:
        return FirstOrderResult(Reason.SUBMISSION_LEDGER_UNAVAILABLE), None
