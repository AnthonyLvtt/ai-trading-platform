"""Synthetic first-order authority/clock/transport; never imported by runtime."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atp.exchange.filters import NotionalPriceEvidence
from atp.exchange.model import decimal_text
from atp.exchange.transport import TransportReply
from atp.first_testnet_order.execution import (
    TESTNET_ENDPOINT,
    FirstOrderTransport,
    consume_submission_permit,
)
from atp.first_testnet_order.gate import (
    ExchangeTimeEvidence,
    FirstOrderInputs,
    GateClock,
    GateTimeSample,
)
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.model import (
    FirstOrderPolicy,
    FirstTestnetOrderAuthorization,
    TrustedFirstOrderAuthorizationEvidence,
)
from atp.first_testnet_order.trust import TrustedFirstOrderAuthority, trust_first_order
from atp.shared.identity import ContentIdentity
from tests.activation_support import complete_chain
from tests.unit.test_risk_engine import NOW


class SyntheticFirstOrderAuthority(TrustedFirstOrderAuthority):
    def __init__(self, authorization):
        self.pin = authorization.content_identity

    @property
    def accepted_authorization_identity(self):
        return self.pin

    def attest(self, authorization):
        return TrustedFirstOrderAuthorizationEvidence(
            authorization.content_identity, FirstOrderPolicy().content_identity, "synthetic-test"
        )


class SyntheticClock(GateClock):
    def __init__(self, at=NOW, skew=0):
        self.at, self.skew = at, skew

    def read(self):
        return GateTimeSample(
            self.at,
            ExchangeTimeEvidence(
                self.at + timedelta(seconds=self.skew),
                self.at,
                ContentIdentity.from_text("synthetic-server-time"),
            ),
        )


class FakeFirstOrderTransport(FirstOrderTransport):
    def __init__(self, source):
        self.source = source
        self.calls = 0
        self.reply = None
        self.host = TESTNET_ENDPOINT
        self.clock = SyntheticClock()

    @property
    def endpoint(self):
        return self.host

    @property
    def credential_source_identity(self):
        return self.source

    def submit(self, permit, order, at, readiness):
        assert consume_submission_permit(
            permit, order, at, self.source, readiness.content_identity, self.clock
        )
        self.calls += 1
        if isinstance(self.reply, BaseException):
            raise self.reply
        return (
            self.reply
            if self.reply is not None
            else TransportReply(
                200,
                dict(
                    symbol=order.symbol,
                    clientOrderId=order.client_order_id,
                    orderId=12,
                    side="BUY",
                    type="MARKET",
                    origQty=decimal_text(order.quantity),
                    status="NEW",
                    transactTime=int(at.timestamp() * 1000),
                ),
                possibly_sent=True,
            )
        )


@pytest.fixture
def first_order(activation, tmp_path):
    _, chain = complete_chain(activation, tmp_path)
    ctx = chain["runtime_authorization"]
    proof = chain["proof"]
    ledger = TestnetSubmissionLedger.create(tmp_path / "submission.sqlite")
    auth = FirstTestnetOrderAuthorization(
        ctx.activation_grant_identity,
        ctx.content_identity,
        ctx.source_commit_sha,
        ctx.release_identity,
        ctx.tq_identity,
        proof.content_identity,
        ledger.content_identity,
        proof.symbol,
        proof.quantity,
        Decimal("100"),
        NOW - timedelta(minutes=1),
        NOW + timedelta(minutes=5),
    )
    receipt = trust_first_order(auth, SyntheticFirstOrderAuthority(auth))
    price = NotionalPriceEvidence(
        "BTCUSDT",
        Decimal("50000"),
        "EXCHANGE_LAST_PRICE",
        ContentIdentity.from_text("synthetic-price"),
        NOW,
        NOW,
        0,
    )
    chain.pop("at")
    chain["filters"] = replace(chain["filters"], observed_at=NOW)
    values = FirstOrderInputs(auth, receipt, **chain, price=price)
    return values, dict(
        clock=SyntheticClock(),
        ledger=ledger,
        transport=FakeFirstOrderTransport(ctx.credential_source_identity),
    )
