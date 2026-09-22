"""Explicit first-order HTTP boundary. Ordinary transport SUBMIT stays disabled."""

from datetime import datetime

from atp.exchange.model import ExchangeOrderRequest, Reason
from atp.exchange.transport import (
    BinanceTestnetHTTPTransport,
    CredentialMaterial,
    ExchangeCredentialsProvider,
    Operation,
    TransportReply,
    runtime_ready,
)
from atp.first_testnet_order.controlled import FinalBoundary
from atp.first_testnet_order.execution import (
    TESTNET_ENDPOINT,
    FirstOrderTransport,
    SubmissionPermit,
    consume_submission_permit,
    submission_permit_time,
)
from atp.first_testnet_order.gate import GateClock
from atp.shared.identity import ContentIdentity


class FirstOrderBinanceTestnetTransport(BinanceTestnetHTTPTransport, FirstOrderTransport):
    def __init__(
        self,
        credentials: ExchangeCredentialsProvider,
        credential_source_identity: ContentIdentity,
        clock: GateClock | None = None,
        boundary: FinalBoundary | None = None,
    ) -> None:
        super().__init__(credentials)
        self._source_identity = credential_source_identity
        self._clock = clock
        self._boundary = boundary

    @property
    def endpoint(self) -> str:
        return TESTNET_ENDPOINT

    @property
    def credential_source_identity(self) -> ContentIdentity:
        return self._source_identity

    def submit(
        self, permit: SubmissionPermit, order: ExchangeOrderRequest, at: datetime, readiness: object
    ) -> TransportReply:
        if not consume_submission_permit(
            permit,
            order,
            at,
            self._source_identity,
            getattr(readiness, "content_identity", None),
            self._clock,
        ):
            return TransportReply(error=Reason.TESTNET_RUNTIME_BLOCKED)
        if not runtime_ready(readiness):
            return TransportReply(error=Reason.TESTNET_NOT_AUTHORIZED)
        pin = permit.content_identity

        def current_time() -> datetime | None:
            if permit.content_identity != pin:
                return None
            return submission_permit_time(permit, self._clock)

        if current_time() is None:
            return TransportReply(error=Reason.TESTNET_RUNTIME_BLOCKED)
        material = self._credentials.load()
        if material is None:
            return TransportReply(error=Reason.CREDENTIALS_UNAVAILABLE)
        if type(material) is not CredentialMaterial or any(
            type(v) is not str or not v for v in (material.api_key, material.api_secret)
        ):
            return TransportReply(error=Reason.INVALID_CREDENTIALS)
        now = current_time()
        if now is None:
            return TransportReply(error=Reason.TESTNET_RUNTIME_BLOCKED)
        if not isinstance(self._boundary, FinalBoundary):
            return TransportReply(error=Reason.TESTNET_RUNTIME_BLOCKED)

        def before_post() -> datetime | None:
            if current_time() is None:
                return None
            return self._boundary.before_post(permit, order) if self._boundary else None

        return self._dispatch(Operation.SUBMIT, order, now, material, before_send=before_post)
