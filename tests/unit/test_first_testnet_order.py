import copy
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atp.exchange.first_order_transport import FirstOrderBinanceTestnetTransport
from atp.exchange.model import Reason as ExchangeReason
from atp.exchange.read_only import verify_record
from atp.exchange.transport import TransportReply
from atp.first_testnet_order.execution import run_first_order
from atp.first_testnet_order.gate import evaluate_first_order
from atp.first_testnet_order.ledger import LedgerUnavailable, TestnetSubmissionLedger
from atp.first_testnet_order.model import (
    FirstOrderPolicy,
    FirstTestnetOrderAuthorization,
    Reason,
    SubmissionState,
)
from atp.first_testnet_order.trust import trust_first_order
from atp.shared.identity import ContentIdentity
from tests.activation_support import activation
from tests.first_order_support import SyntheticClock, SyntheticFirstOrderAuthority, first_order
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "first_order", "inputs"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_check_only_and_one_attempt(first_order):
    values, deps = first_order
    a = run_first_order(values, **deps)
    assert a.reason_code is Reason.READY_TO_SUBMIT
    assert a == run_first_order(values, **deps)
    assert deps["transport"].calls == 0
    assert not deps["ledger"].consumed(
        values.authorization.content_identity, values.authorization.client_order_id
    )
    result = run_first_order(values, **deps, execute=True)
    assert result.state is SubmissionState.ACKNOWLEDGED
    assert result.reason_code is Reason.RECONCILIATION_REQUIRED
    assert deps["transport"].calls == 1
    restarted = TestnetSubmissionLedger(deps["ledger"].path)
    deps["ledger"] = restarted
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.FIRST_ORDER_ALREADY_CONSUMED
    )
    assert deps["transport"].calls == 1


def test_identity_levels(first_order):
    auth = first_order[0].authorization
    same = replace(auth)
    changed = replace(auth, quantity=auth.quantity * 2)
    for key in ("authorization_facts_identity", "client_order_id", "content_identity"):
        assert getattr(same, key) == getattr(auth, key)
        assert getattr(changed, key) != getattr(auth, key)
    altered = copy.copy(auth)
    object.__setattr__(altered, "client_order_id", "atp-wrong")
    assert (
        trust_first_order(altered, SyntheticFirstOrderAuthority(altered))
        is Reason.FIRST_ORDER_AUTHORIZATION_INVALID
    )


@pytest.mark.parametrize(
    "field",
    [
        "authorization_facts_identity",
        "content_identity",
        "quantity",
        "max_quote_notional",
        "valid_until",
        "max_submissions",
    ],
)
def test_prepinning_tamper(first_order, field):
    auth = copy.copy(first_order[0].authorization)
    object.__setattr__(auth, field, None)
    assert not verify_record(auth, FirstTestnetOrderAuthorization)
    assert trust_first_order(auth) is Reason.FIRST_ORDER_AUTHORIZATION_INVALID


def test_untrusted_and_copy_receipts(first_order):
    values, deps = first_order
    assert trust_first_order(values.authorization) is Reason.FIRST_ORDER_AUTHORIZATION_UNTRUSTED
    other = replace(values.authorization, max_quote_notional=Decimal("99"))
    assert (
        trust_first_order(values.authorization, SyntheticFirstOrderAuthority(other))
        is Reason.FIRST_ORDER_AUTHORIZATION_UNTRUSTED
    )
    for receipt in (None, copy.copy(values.receipt)):
        assert (
            run_first_order(replace(values, receipt=receipt), **deps).reason_code
            is Reason.FIRST_ORDER_AUTHORIZATION_UNTRUSTED
        )


@pytest.mark.parametrize(
    "field",
    [
        "proof",
        "risk",
        "strategy",
        "grant",
        "runtime_authorization",
        "readiness",
        "release",
        "wheel",
        "promotion",
        "filters",
        "price",
    ],
)
def test_missing_gate(first_order, field):
    values, deps = first_order
    result = run_first_order(replace(values, **{field: None}), **deps, execute=True)
    assert result.status == "BLOCKED"
    assert deps["transport"].calls == 0
    assert not deps["ledger"].consumed(
        values.authorization.content_identity, values.authorization.client_order_id
    )


@pytest.mark.parametrize(
    "field",
    [
        "activation_grant_identity",
        "runtime_authorization_context_identity",
        "release_identity",
        "tq_identity",
        "source_commit_sha",
    ],
)
def test_wrong_binding(first_order, field):
    values, deps = first_order
    changed = "f" * 40 if field == "source_commit_sha" else ContentIdentity.from_text("other")
    auth = replace(values.authorization, **{field: changed})
    receipt = trust_first_order(auth, SyntheticFirstOrderAuthority(auth))
    assert (
        run_first_order(replace(values, authorization=auth, receipt=receipt), **deps).reason_code
        is Reason.FIRST_ORDER_AUTHORIZATION_MISMATCH
    )


@pytest.mark.parametrize(
    ("which", "age", "reason"),
    [
        ("filter", 900, Reason.READY_TO_SUBMIT),
        ("filter", 901, Reason.SYMBOL_FILTER_EVIDENCE_STALE),
        ("filter", -6, Reason.INVALID_FILTER_EVIDENCE),
        ("price", 10, Reason.READY_TO_SUBMIT),
        ("price", 11, Reason.PRICE_EVIDENCE_STALE),
        ("price", -6, Reason.TIME_EVIDENCE_INVALID),
    ],
)
def test_freshness(first_order, which, age, reason):
    values, deps = first_order
    when = NOW - timedelta(seconds=age)
    if which == "filter":
        values = replace(values, filters=replace(values.filters, observed_at=when))
    else:
        values = replace(values, price=replace(values.price, observed_at=when, effective_at=when))
    assert run_first_order(values, **deps).reason_code is reason
    assert deps["transport"].calls == 0


@pytest.mark.parametrize("skew", [-6, 6])
def test_time_skew(first_order, skew):
    values, deps = first_order
    deps["clock"] = SyntheticClock(skew=skew)
    assert run_first_order(values, **deps).reason_code is Reason.TIME_EVIDENCE_INVALID


def test_freshness_rechecked_before_reservation(first_order):
    values, deps = first_order

    class AgingClock(SyntheticClock):
        def read(self):
            value = super().read()
            self.at += timedelta(seconds=11)
            return value

    deps["clock"] = AgingClock()
    assert run_first_order(values, **deps, execute=True).reason_code is Reason.PRICE_EVIDENCE_STALE
    assert not deps["ledger"].consumed(
        values.authorization.content_identity, values.authorization.client_order_id
    )
    assert deps["transport"].calls == 0


def test_notional_cap_no_consumption(first_order):
    values, deps = first_order
    auth = replace(values.authorization, max_quote_notional=Decimal("49.999999999999999999999"))
    receipt = trust_first_order(auth, SyntheticFirstOrderAuthority(auth))
    result = run_first_order(
        replace(values, authorization=auth, receipt=receipt), **deps, execute=True
    )
    assert result.reason_code is Reason.NOTIONAL_LIMIT_EXCEEDED
    assert not deps["ledger"].consumed(auth.content_identity, auth.client_order_id)
    assert deps["transport"].calls == 0


@pytest.mark.parametrize(
    "reply",
    [
        TimeoutError("api_secret=synthetic-canary"),
        TransportReply(error=ExchangeReason.TRANSPORT_TIMEOUT, possibly_sent=True),
        TransportReply(200, {"nonsense": "api_secret=synthetic-canary"}),
        TransportReply(503, {"message": "synthetic-canary"}),
    ],
)
def test_unknown_never_retries_or_leaks(first_order, reply, caplog):
    values, deps = first_order
    deps["transport"].reply = reply
    result = run_first_order(values, **deps, execute=True)
    assert result.state is SubmissionState.UNKNOWN
    assert result.transport_call_count == 1
    assert "synthetic-canary" not in repr(result) + caplog.text
    assert b"synthetic-canary" not in deps["ledger"].path.read_bytes()
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.FIRST_ORDER_ALREADY_CONSUMED
    )
    assert deps["transport"].calls == 1


def test_failed_persistence(first_order, monkeypatch):
    values, deps = first_order

    def fail(*args, **kwargs):
        raise LedgerUnavailable()

    monkeypatch.setattr(deps["ledger"], "append", fail)
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.SUBMISSION_LEDGER_UNAVAILABLE
    )
    assert deps["transport"].calls == 0


def test_parallel_reservation(first_order):
    values, deps = first_order
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run_first_order(values, **deps, execute=True), range(2)))
    assert sorted(r.reason_code for r in results) == sorted(
        [Reason.FIRST_ORDER_ALREADY_CONSUMED, Reason.RECONCILIATION_REQUIRED]
    )
    assert deps["transport"].calls == 1


def test_crash_after_reservation(first_order):
    values, deps = first_order
    deps["transport"].reply = RuntimeError("internal programming defect")
    with pytest.raises(RuntimeError):
        run_first_order(values, **deps, execute=True)
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.FIRST_ORDER_ALREADY_CONSUMED
    )


def test_no_ledger_reset(first_order):
    _, deps = first_order
    with pytest.raises(LedgerUnavailable):
        TestnetSubmissionLedger.create(deps["ledger"].path)
    with sqlite3.connect(deps["ledger"].path) as db:
        # SQL-level defenses accompany the read-only API.
        assert (
            db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 2
        )


@pytest.mark.parametrize("value", [None, "bad", [], object()])
def test_malformed_runtime(value):
    assert run_first_order(value).status == "BLOCKED"


def test_http_requires_process_permit(first_order):
    values, deps = first_order
    _, order, at = evaluate_first_order(values, deps["clock"])

    class Provider:
        def load(self):
            pytest.fail("Credentials must not be loaded")

    transport = FirstOrderBinanceTestnetTransport(Provider(), deps["transport"].source)
    assert (
        transport.submit(None, order, at, values.readiness).error
        is ExchangeReason.TESTNET_RUNTIME_BLOCKED
    )


def test_mutated_policy(first_order):
    values, deps = first_order
    policy = FirstOrderPolicy()
    object.__setattr__(policy, "max_submissions", 2)
    assert run_first_order(values, **deps, policy=policy).status == "BLOCKED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("side", "SELL"),
        ("order_type", "LIMIT"),
        ("symbol", "*"),
        ("symbol", "ALL"),
        ("max_submissions", 2),
        ("quantity", Decimal(0)),
        ("quantity", Decimal("NaN")),
        ("quantity", Decimal("Infinity")),
    ],
)
def test_invalid_scope_cannot_be_constructed(first_order, field, value):
    with pytest.raises(ValueError):
        replace(first_order[0].authorization, **{field: value})


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"valid_until": NOW}, Reason.FIRST_ORDER_AUTHORIZATION_EXPIRED),
        ({"valid_from": NOW + timedelta(seconds=1)}, Reason.FIRST_ORDER_NOT_READY),
        ({"quantity": Decimal("0.002")}, Reason.QUANTITY_NOT_AUTHORIZED),
        ({"symbol": "ETHUSDT"}, Reason.FIRST_ORDER_AUTHORIZATION_MISMATCH),
    ],
)
def test_authorization_scope_at_gate(first_order, change, reason):
    values, deps = first_order
    auth = replace(values.authorization, **change)
    receipt = trust_first_order(auth, SyntheticFirstOrderAuthority(auth))
    assert (
        run_first_order(replace(values, authorization=auth, receipt=receipt), **deps).reason_code
        is reason
    )
    assert deps["transport"].calls == 0


@pytest.mark.parametrize(
    ("endpoint", "reason"),
    [
        ("https://api.binance.com", Reason.LIVE_FORBIDDEN),
        ("https://example.invalid", Reason.TESTNET_ENDPOINT_MISMATCH),
        ("http://testnet.binance.vision", Reason.LIVE_FORBIDDEN),
    ],
)
def test_endpoint_binding(first_order, endpoint, reason):
    values, deps = first_order
    deps["transport"].host = endpoint
    assert run_first_order(values, **deps, execute=True).reason_code is reason
    assert deps["transport"].calls == 0


def test_other_ledger_cannot_reset_authorization(first_order, tmp_path):
    values, deps = first_order
    deps["ledger"] = TestnetSubmissionLedger.create(tmp_path / "another.sqlite")
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.SUBMISSION_LEDGER_UNAVAILABLE
    )
    assert deps["transport"].calls == 0


def test_ledger_missing_or_corrupt_blocks(first_order):
    values, deps = first_order
    deps["ledger"].path.write_bytes(b"not a database")
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.SUBMISSION_LEDGER_UNAVAILABLE
    )
    deps["ledger"].path.unlink()
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.SUBMISSION_LEDGER_UNAVAILABLE
    )
    assert deps["transport"].calls == 0


def test_authentication_rejection_is_certain_and_consumed(first_order, caplog):
    values, deps = first_order
    deps["transport"].reply = TransportReply(401, {"code": -2015, "msg": "synthetic-canary"})
    result = run_first_order(values, **deps, execute=True)
    assert result.reason_code is Reason.EXCHANGE_REJECTED
    assert result.exchange_status == "REJECTED"
    assert result.state is not SubmissionState.UNKNOWN
    assert "synthetic-canary" not in repr(result) + caplog.text
    assert b"synthetic-canary" not in deps["ledger"].path.read_bytes()
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.FIRST_ORDER_ALREADY_CONSUMED
    )


def test_no_notional_filter_still_requires_exchange_price(first_order):
    import json

    from atp.exchange.filters import parse_symbol_filters

    values, deps = first_order
    data = json.loads(values.filters.payload)
    data["filters"] = [
        f for f in data["filters"] if f["filterType"] not in ("NOTIONAL", "MIN_NOTIONAL")
    ]
    values = replace(values, filters=parse_symbol_filters(data, NOW))
    assert run_first_order(values, **deps).reason_code is Reason.READY_TO_SUBMIT
    for price in (
        None,
        replace(values.price, source_type="DATA_MARK"),
        replace(values.price, symbol="ETHUSDT"),
    ):
        assert run_first_order(replace(values, price=price), **deps).status == "BLOCKED"


def test_average_price_window_required(first_order):
    import json

    from atp.exchange.filters import parse_symbol_filters

    values, deps = first_order
    data = json.loads(values.filters.payload)
    data["filters"] = [
        f for f in data["filters"] if f["filterType"] not in ("NOTIONAL", "MIN_NOTIONAL")
    ]
    data["filters"].append(
        dict(filterType="MIN_NOTIONAL", minNotional="10", applyToMarket=True, avgPriceMins=5)
    )
    values = replace(values, filters=parse_symbol_filters(data, NOW))
    assert run_first_order(values, **deps).status == "BLOCKED"
    values = replace(
        values,
        price=replace(values.price, source_type="EXCHANGE_AVERAGE_PRICE", avg_price_minutes=5),
    )
    assert run_first_order(values, **deps).reason_code is Reason.READY_TO_SUBMIT


def test_skew_parser_and_ephemeral_evaluation_time():
    from atp.exchange.time_evidence import parse_server_time_evidence
    from atp.first_testnet_order.gate import GateTimeSample

    proof = parse_server_time_evidence({"serverTime": int(NOW.timestamp() * 1000)}, NOW)
    assert proof is not None
    assert (
        GateTimeSample(NOW, proof).evidence.content_identity
        == GateTimeSample(NOW + timedelta(seconds=1), proof).evidence.content_identity
    )
    assert parse_server_time_evidence({"serverTime": "bad"}, NOW) is None
    assert parse_server_time_evidence({"serverTime": 1, "secret": "canary"}, NOW) is None


def test_low_decimal_context_does_not_round_cap(first_order):
    from decimal import localcontext

    values, deps = first_order
    auth = replace(values.authorization, max_quote_notional=Decimal("49.999999999999999999999"))
    values = replace(
        values,
        authorization=auth,
        receipt=trust_first_order(auth, SyntheticFirstOrderAuthority(auth)),
    )
    with localcontext() as ctx:
        ctx.prec = 2
        assert run_first_order(values, **deps).reason_code is Reason.NOTIONAL_LIMIT_EXCEEDED
