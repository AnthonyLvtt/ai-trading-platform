from __future__ import annotations

import ast
import http.client as http_client
import inspect
import socket
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import pytest

from atp.exchange import (
    BinanceTestnetHTTPTransport,
    EnvironmentCredentialsProvider,
    ExchangeAdapter,
    ExchangePolicy,
    Reason,
    Side,
    Status,
    authorize_testnet,
    request_from_proof,
)
from atp.exchange.model import identity
from atp.exchange.transport import CredentialMaterial, Operation, TransportReply
from atp.shared.errors import ValidationError
from tests.exchange_support import FakeOnlyAdapter, FakeTransport, inputs, record


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.mark.parametrize("side", list(Side))
def test_real_risk_to_fake_mapping(side):
    order, args = inputs(side)
    transport = FakeTransport()
    result = FakeOnlyAdapter(transport).submit(order, **args)
    assert result.status is Status.ACCEPTED
    assert result.content_identity == identity(result)
    assert transport.calls[0][0] is Operation.SUBMIT
    wire = transport.calls[0][1]
    assert wire["side"] == side.value and wire["type"] == "MARKET"
    assert wire["quantity"] == "0.001"
    assert wire["newClientOrderId"] == order.client_order_id


def test_runtime_never_accepts_test_risk_even_with_fixture():
    order, args = inputs()
    transport = FakeTransport()
    assert (
        ExchangeAdapter(transport).submit(order, **args).reason_code
        is Reason.RISK_AUTHORIZATION_INVALID
    )
    assert not transport.calls
    assert authorize_testnet(None) is None


def test_fake_exception_cannot_use_http_transport():
    order, args = inputs()
    http = BinanceTestnetHTTPTransport(EnvironmentCredentialsProvider({}))
    assert FakeOnlyAdapter(http).submit(order, **args).reason_code is Reason.TESTNET_NOT_AUTHORIZED
    assert (
        http.perform(Operation.SUBMIT, order, args["submitted_at"], None).error
        is Reason.TESTNET_NOT_AUTHORIZED
    )


@pytest.mark.parametrize("name", ["authorization", "proof", "risk", "strategy", "submitted_at"])
@pytest.mark.parametrize("value", [None, "invalid", 42, [], object()])
def test_malformed_external_evidence(name, value):
    order, args = inputs()
    args[name] = value
    transport = FakeTransport()
    adapter = FakeOnlyAdapter(transport)
    first = adapter.submit(order, **args)
    assert first == adapter.submit(order, **args)
    assert first.status is Status.BLOCKED and not transport.calls


@pytest.mark.parametrize(
    "value", [None, "1", Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")]
)
def test_bad_quantity(value):
    order, args = inputs()
    object.__setattr__(order, "quantity", value)
    assert (
        FakeOnlyAdapter(FakeTransport()).submit(order, **args).reason_code
        is Reason.INVALID_QUANTITY
    )


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("risk", "status", "APPROVED"),
        ("risk", "reason_code", "POLICY_COMPLIANT"),
        ("proof", "quantity", Decimal(2)),
        ("authorization", "environment", "LIVE"),
        ("strategy", "content_identity", None),
    ],
)
def test_tampered_proofs(target, field, value):
    order, args = inputs()
    object.__setattr__(args[target], field, value)
    assert FakeOnlyAdapter(FakeTransport()).submit(order, **args).status is Status.BLOCKED


@pytest.mark.parametrize("field", [f.name for f in fields(ExchangePolicy)])
def test_policy_locked_and_revalidated(field):
    policy = ExchangePolicy()
    bad = not getattr(policy, field) if type(getattr(policy, field)) is bool else "altered"
    with pytest.raises(ValidationError):
        replace(policy, **{field: bad})
    object.__setattr__(policy, field, bad)
    order, args = inputs()
    assert (
        FakeOnlyAdapter(FakeTransport(), policy).submit(order, **args).reason_code
        is Reason.EXCHANGE_POLICY_MISMATCH
    )


def test_live_always_blocks():
    order, args = inputs()
    object.__setattr__(order, "environment", "LIVE")
    assert (
        ExchangeAdapter(FakeTransport()).submit(order, **args).reason_code is Reason.LIVE_FORBIDDEN
    )


def test_identity_external_quantity_and_duplicates():
    order, args = inputs()
    assert order == request_from_proof(args["proof"])
    proof = args["proof"]
    data = {f.name: getattr(proof, f.name) for f in fields(proof) if f.name != "content_identity"}
    data["quantity"] = Decimal("0.002")
    other = request_from_proof(record(type(proof), **data))
    assert other.client_order_id != order.client_order_id and len(order.client_order_id) == 36
    transport = FakeTransport()
    adapter = FakeOnlyAdapter(transport)
    first = adapter.submit(order, **args)
    assert adapter.submit(order, **args) == first
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "reply,status,reason",
    [
        (
            TransportReply(400, {"code": -2010, "msg": "sensitive discarded"}),
            Status.REJECTED,
            Reason.EXCHANGE_REJECTED,
        ),
        (TransportReply(200, {}), Status.UNCERTAIN, Reason.MALFORMED_EXCHANGE_RESPONSE),
        (TransportReply(503, {}), Status.UNCERTAIN, Reason.SUBMISSION_OUTCOME_UNKNOWN),
        (TransportReply(400, {"code": -1007}), Status.UNCERTAIN, Reason.SUBMISSION_OUTCOME_UNKNOWN),
        (
            TransportReply(error=Reason.TRANSPORT_TIMEOUT, possibly_sent=True),
            Status.UNCERTAIN,
            Reason.SUBMISSION_OUTCOME_UNKNOWN,
        ),
        (
            TransportReply(error=Reason.TRANSPORT_TIMEOUT),
            Status.BLOCKED,
            Reason.TRANSPORT_UNAVAILABLE,
        ),
        (
            TransportReply(error=Reason.CREDENTIALS_UNAVAILABLE),
            Status.BLOCKED,
            Reason.CREDENTIALS_UNAVAILABLE,
        ),
        (
            TransportReply(error=Reason.INVALID_CREDENTIALS),
            Status.BLOCKED,
            Reason.INVALID_CREDENTIALS,
        ),
        (object(), Status.UNCERTAIN, Reason.MALFORMED_EXCHANGE_RESPONSE),
    ],
)
def test_outcome_mapping(reply, status, reason):
    order, args = inputs()
    result = FakeOnlyAdapter(FakeTransport([reply])).submit(order, **args)
    assert result.status is status and result.reason_code is reason
    assert "sensitive discarded" not in repr(result)


def test_uncertain_only_queries_never_reposts():
    order, args = inputs()
    transport = FakeTransport([TransportReply(error=Reason.TRANSPORT_TIMEOUT, possibly_sent=True)])
    adapter = FakeOnlyAdapter(transport)
    assert adapter.submit(order, **args).status is Status.UNCERTAIN
    assert len(transport.calls) == 1
    assert adapter.submit(order, **args).status is Status.ACCEPTED
    assert [c[0] for c in transport.calls] == [Operation.SUBMIT, Operation.QUERY]
    assert transport.calls[-1][1]["origClientOrderId"] == order.client_order_id


def test_malformed_and_not_found_reconciliation_do_not_enable_resubmit():
    order, args = inputs()
    transport = FakeTransport(
        [
            TransportReply(200, {}),
            TransportReply(200, {}),
            TransportReply(400, {"code": -2013}),
            TransportReply(200, {}),
        ]
    )
    adapter = FakeOnlyAdapter(transport)
    assert adapter.submit(order, **args).status is Status.UNCERTAIN
    assert adapter.reconcile(order, **args).status is Status.BLOCKED
    assert adapter.submit(order, **args).status is Status.UNCERTAIN
    adapter.submit(order, **args)
    assert [c[0] for c in transport.calls].count(Operation.SUBMIT) == 1


@pytest.mark.parametrize(
    "data",
    [{}, {"ATP_BINANCE_TESTNET_API_KEY": "dummy"}, {"ATP_BINANCE_TESTNET_API_SECRET": "dummy"}],
)
def test_credential_provider_incomplete(data):
    assert EnvironmentCredentialsProvider(data).load() is None


def test_credentials_not_in_repr():
    provider = EnvironmentCredentialsProvider(
        {"ATP_BINANCE_TESTNET_API_KEY": "dummykey", "ATP_BINANCE_TESTNET_API_SECRET": "dummysecret"}
    )
    material = provider.load()
    assert type(material) is CredentialMaterial
    assert "dummykey" not in repr(material) + repr(provider)
    assert "dummysecret" not in repr(material) + repr(provider)


def test_closed_authority_surface():
    forbidden = {"withdraw", "transfer", "internal_transfer", "margin_borrow", "margin_repay"}
    for cls in (ExchangeAdapter, BinanceTestnetHTTPTransport):
        assert not forbidden.intersection(dir(cls))
        assert not {"base_url", "live", "production", "endpoint"}.intersection(
            inspect.signature(cls).parameters
        )
    for path in Path("src/atp/exchange").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("atp.oms", "atp.ai", "atp.accounting", "atp.strategy.sma", "atp.risk.engine")
                )
    source = Path("src/atp/exchange/transport.py").read_text()
    assert (
        'HTTPSConnection("testnet.binance.vision"' in source
        or '"testnet.binance.vision",' in source
    )
    assert '"/api/v3/order"' in source and '"/api/v3/ping"' in source


@pytest.mark.parametrize("phase", ["connect", "request", "response", "success", "malformed"])
def test_http_wire_with_local_connection_double(monkeypatch, phase):
    """Exercise signing/wire errors offline; never bypass OPS with a real connection."""
    import hashlib
    import hmac
    import json
    from urllib.parse import parse_qs

    order, args = inputs()
    calls = []

    class Connection:
        def __init__(self, host, **kwargs):
            assert host == "testnet.binance.vision"

        def connect(self):
            if phase == "connect":
                raise TimeoutError()

        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))
            if phase == "request":
                raise TimeoutError()

        def getresponse(self):
            if phase == "response":
                raise TimeoutError()
            return self

        status = 200

        def read(self, limit):
            return b"invalid" if phase == "malformed" else json.dumps({}).encode()

        def close(self):
            pass

    monkeypatch.setattr(http_client, "HTTPSConnection", Connection)
    material = CredentialMaterial("dummykey", "dummysecret")
    http = BinanceTestnetHTTPTransport(EnvironmentCredentialsProvider({}))
    reply = http._dispatch(Operation.SUBMIT, order, args["submitted_at"], material)
    if phase in ("connect", "request", "response"):
        assert reply.error is Reason.TRANSPORT_TIMEOUT
        assert reply.possibly_sent is (phase != "connect")
    elif phase == "malformed":
        assert reply.error is Reason.MALFORMED_EXCHANGE_RESPONSE
    else:
        assert reply.http_status == 200
    if calls:
        method, path, body, headers = calls[0]
        assert method == "POST" and path == "/api/v3/order"
        unsigned, signature = body.rsplit("&signature=", 1)
        assert signature == hmac.new(b"dummysecret", unsigned.encode(), hashlib.sha256).hexdigest()
        assert parse_qs(unsigned)["newClientOrderId"] == [order.client_order_id]
        assert headers["X-MBX-APIKEY"] == "dummykey"
    assert "dummysecret" not in repr(reply) and "dummykey" not in repr(reply)


@pytest.mark.parametrize(
    "field,value",
    [
        ("orderId", True),
        ("side", 42),
        ("status", []),
        ("origQty", "NaN"),
        ("transactTime", {}),
        ("clientOrderId", object()),
    ],
)
def test_malformed_response_values(field, value):
    order, args = inputs()
    fake = FakeTransport()
    reply = fake.perform(Operation.SUBMIT, order, args["submitted_at"], None)
    reply.body[field] = value
    result = FakeOnlyAdapter(FakeTransport([reply])).submit(order, **args)
    assert result.status is Status.UNCERTAIN


def test_internal_exception_does_not_allow_second_post():
    order, args = inputs()

    class Failure:
        pass

    fake = FakeTransport([Failure()])
    adapter = FakeOnlyAdapter(fake)
    assert adapter.submit(order, **args).status is Status.UNCERTAIN
    adapter.submit(order, **args)
    assert [c[0] for c in fake.calls] == [Operation.SUBMIT, Operation.QUERY]


def test_connectivity_is_not_readiness_or_authorization():
    _, args = inputs()
    transport = FakeTransport()
    result = ExchangeAdapter(transport).check_connectivity(
        authorization=args["authorization"], readiness=None, checked_at=args["submitted_at"]
    )
    assert not result.available and result.reason_code is Reason.TESTNET_NOT_AUTHORIZED
    assert not transport.calls


@pytest.mark.parametrize("bad", [None, "order", 123, [], object()])
def test_malformed_top_level_order(bad):
    _, args = inputs()
    assert FakeOnlyAdapter(FakeTransport()).submit(bad, **args).status is Status.BLOCKED


def test_deleted_order_field_blocks():
    order, args = inputs()
    object.__delattr__(order, "environment")
    assert FakeOnlyAdapter(FakeTransport()).submit(order, **args).status is Status.BLOCKED


@pytest.mark.parametrize(
    "status,reason", [("BLOCKED", "PORTFOLIO_STATE_UNKNOWN"), ("REJECTED", "MAX_POSITIONS_REACHED")]
)
def test_nonapproved_risk_cannot_submit(status, reason):
    from atp.risk import RiskDecision, RiskReasonCode, RiskStatus

    order, args = inputs()
    args["risk"] = RiskDecision.create(
        status=RiskStatus(status),
        reason_code=RiskReasonCode(reason),
        provenance=args["risk"].provenance,
    )
    fake = FakeTransport()
    assert (
        FakeOnlyAdapter(fake).submit(order, **args).reason_code is Reason.RISK_AUTHORIZATION_INVALID
    )
    assert not fake.calls


def test_self_consistent_risk_with_incompatible_market_is_not_blessed():
    from atp.risk import RiskDecision

    order, args = inputs()
    old = args["risk"]
    market = replace(old.provenance.market_context, margin_enabled=True)
    provenance = replace(
        old.provenance, market_context=market, market_context_identity=market.content_identity
    )
    risk = RiskDecision.create(
        status=old.status, reason_code=old.reason_code, provenance=provenance
    )
    proof = args["proof"]
    data = {f.name: getattr(proof, f.name) for f in fields(proof) if f.name != "content_identity"}
    data.update(
        risk_decision_identity=risk.content_identity, risk_decision_id=str(risk.risk_decision_id)
    )
    args["proof"] = record(type(proof), **data)
    args["risk"] = risk
    order = request_from_proof(args["proof"])
    assert (
        FakeOnlyAdapter(FakeTransport()).submit(order, **args).reason_code
        is Reason.RISK_AUTHORIZATION_INVALID
    )


@pytest.mark.parametrize("secret", ["dummy-secret_+/=", "dummy-sécret_+/="])
def test_non_alphanumeric_credentials_reach_auth_boundary_without_leakage(
    monkeypatch, caplog, capsys, secret
):
    import atp.exchange.transport as transport_module

    order, args = inputs()
    key = "dummy-key_+/="
    provider = EnvironmentCredentialsProvider(
        {"ATP_BINANCE_TESTNET_API_KEY": key, "ATP_BINANCE_TESTNET_API_SECRET": secret}
    )
    calls = []

    def local_dispatch(self, operation, received_order, at, material):
        assert material.api_key == key and material.api_secret == secret
        assert operation is Operation.SUBMIT and received_order == order
        calls.append(operation)
        # Even an authentication error containing sensitive text must not reach domain results.
        return TransportReply(401, {"code": -2015, "msg": key + secret}, possibly_sent=True)

    # Isolate credential validation only, with dispatch replaced and the socket guard active.
    # This test patch is not part of runtime or the fake contract's environment exception.
    monkeypatch.setattr(transport_module, "runtime_ready", lambda _: True)
    monkeypatch.setattr(BinanceTestnetHTTPTransport, "_dispatch", local_dispatch)
    reply = BinanceTestnetHTTPTransport(provider).perform(
        Operation.SUBMIT, order, args["submitted_at"], None
    )
    assert calls == [Operation.SUBMIT] and reply.error is None
    result = ExchangeAdapter(FakeTransport())._map(reply, order, args["submitted_at"], False)
    assert result.status is Status.REJECTED
    assert result.reason_code is Reason.EXCHANGE_REJECTED
    output = capsys.readouterr()
    visible = repr(provider) + repr(provider.load()) + repr(reply) + repr(result)
    visible += caplog.text + output.out + output.err
    assert key not in visible and secret not in visible
