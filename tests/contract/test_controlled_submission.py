"""008A operator wiring with synthetic approvals and an in-memory HTTP boundary only."""

import json
import multiprocessing
import shutil
import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atp.exchange.transport import CredentialMaterial
from atp.first_testnet_order import controlled, preparation_runtime
from atp.first_testnet_order.execution import submission_permit_time
from atp.first_testnet_order.ledger import (
    AlreadyConsumed,
    LedgerUnavailable,
    TestnetSubmissionLedger,
)
from atp.first_testnet_order.model import Reason, SubmissionState
from atp.first_testnet_order.reconciliation import reconcile_first_order
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.runtime_credentials import (
    ReferencedEnvironmentCredentialsProvider,
    new_credential_reference,
)
from tests.activation_support import activation
from tests.contract.test_check_only_preparation import OfflineSource, millis
from tests.first_order_support import first_order
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs", "first_order"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network is forbidden in 008A")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def operator(activation, tmp_path, monkeypatch):
    reference = new_credential_reference()
    from atp.exchange.transport import EnvironmentCredentialsProvider

    monkeypatch.setattr(
        EnvironmentCredentialsProvider,
        "load",
        lambda _: CredentialMaterial("synthetic", "synthetic"),
    )
    provider = ReferencedEnvironmentCredentialsProvider(reference)
    path = tmp_path / "campaign.sqlite"
    monkeypatch.setattr(controlled, "OPERATIONAL_LEDGER_PATH", path)
    ledger = TestnetSubmissionLedger.create(path)
    artifacts = {}
    clock = [NOW]

    class Source(OfflineSource):
        fault = None
        accounts = 0

        def read(self, resource, parameters=()):
            if resource == "time":
                return {"serverTime": millis(clock[0])}
            if resource == "account":
                self.accounts += 1
                if self.accounts > 1 and self.fault == "account_slow":
                    clock[0] += timedelta(seconds=11)
                if self.accounts > 1 and self.fault == "risk":
                    self.responses["account"]["balances"][0]["free"] = "0.001"
            if self.accounts > 1:
                if resource == "trades":
                    if self.fault == "orders_slow":
                        clock[0] += timedelta(seconds=11)
                    age = 11 if self.fault == "price" else 0
                    return [{"price": "50000", "time": millis(clock[0] - timedelta(seconds=age))}]
                if resource == "openOrders" and self.fault == "partial_orders":
                    return {"partial": True}
                if resource == "trades" and self.fault == "orders_slow":
                    clock[0] += timedelta(seconds=11)
            return super().read(resource, parameters)

    source = Source()
    source.responses["exchangeInfo"]["symbols"][0]["filters"].append(
        {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200}
    )
    (tmp_path / "artifacts").mkdir()
    posts = []
    http_fault = [None]

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            if http_fault[0] == "changed_after_connect":
                source.fault = "risk"

        def request(self, method, path, body=None, headers=None):
            assert method == "POST" and path == "/api/v3/order"
            posts.append(body)
            if http_fault[0] == "unknown":
                raise TimeoutError()

        def getresponse(self):
            return self

        status = 200

        def read(self, size):
            auth = artifacts["first-order-authorization"]
            return json.dumps(
                dict(
                    symbol="BTCUSDT",
                    clientOrderId=auth.client_order_id,
                    orderId=12,
                    side="BUY",
                    type="MARKET",
                    origQty=str(auth.quantity),
                    status="NEW",
                    transactTime=millis(clock[0]),
                )
            ).encode()

        def close(self):
            pass

    monkeypatch.setattr("http.client.HTTPSConnection", Connection)

    def pin(kind):
        return artifacts[kind].content_identity

    kwargs = dict(
        source=source,
        now=lambda: clock[0],
        release=activation["release"],
        wheel=activation["wheel"],
        tq=activation["tq"],
        tq_evidence=activation["tq_evidence"],
        credential_source_identity=reference.content_identity,
        credential_authority=activation["credential_authority"],
        workspace=tmp_path,
        ledger=ledger,
        artifact_sink=lambda kind, obj: artifacts.__setitem__(kind, obj),
        trust_pin_source=pin,
    )
    return kwargs, provider, source, artifacts, posts, http_fault, clock


def test_operator_refreshes_after_connect_and_submits_once(operator):
    kwargs, provider, source, artifacts, posts, _, _ = operator
    report = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert report["status"] == "ACKNOWLEDGED", report
    assert len(posts) == 1 and source.accounts == 3
    assert artifacts["first-order-authorization"].max_quote_notional == Decimal("6")
    assert kwargs["ledger"].inspect_campaign()
    # A new candidate in another evaluation cannot reset campaign consumption.
    again = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert again["reason_code"] == "FIRST_ORDER_ALREADY_CONSUMED"
    assert len(posts) == 1


@pytest.mark.parametrize(
    "fault", ["price", "account_slow", "orders_slow", "risk", "partial_orders"]
)
def test_final_refresh_blocks_without_reservation(operator, fault):
    kwargs, provider, source, _, posts, _, _ = operator
    source.fault = fault
    result = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert result["status"] == "BLOCKED", result
    assert not posts and not kwargs["ledger"].inspect_campaign()


def test_changed_account_after_connect_never_posts(operator):
    kwargs, provider, _, _, posts, fault, _ = operator
    fault[0] = "changed_after_connect"
    result = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert result["status"] == "UNKNOWN", result
    assert not posts and kwargs["ledger"].inspect_campaign()


def test_unknown_consumes_entire_campaign_across_restart(operator):
    kwargs, provider, _, artifacts, posts, fault, _ = operator
    fault[0] = "unknown"
    result = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert result["status"] == "UNKNOWN" and len(posts) == 1
    reopened = TestnetSubmissionLedger(kwargs["ledger"].path)
    assert reopened.inspect_campaign()
    with pytest.raises(AlreadyConsumed):
        reopened.append(
            ContentIdentity.from_text("other authorization"),
            "different-client",
            SubmissionState.ATTEMPT_STARTED,
        )
    assert len(posts) == 1


def test_failed_commit_never_constructs_transport(operator, monkeypatch):
    kwargs, provider, _, _, posts, _, _ = operator

    def unavailable(*args, **kw):
        raise LedgerUnavailable("synthetic failure")

    monkeypatch.setattr(kwargs["ledger"], "append", unavailable)
    result = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert result["status"] == "BLOCKED" and not posts
    assert not kwargs["ledger"].inspect_campaign()


@pytest.mark.parametrize(
    "field",
    [
        "authorization",
        "receipt",
        "risk",
        "strategy",
        "grant",
        "runtime_authorization",
        "readiness",
        "release",
        "wheel",
        "promotion",
        "proof",
    ],
)
def test_operator_missing_gate_never_posts(operator, monkeypatch, field):
    kwargs, provider, _, _, posts, _, _ = operator
    real = controlled.execute_controlled

    def altered(values, portfolio, **deps):
        return real(replace(values, **{field: None}), portfolio, **deps)

    monkeypatch.setattr(controlled, "execute_controlled", altered)
    result = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert result["status"] == "BLOCKED" and not posts


@pytest.mark.parametrize("change", ["old_cap", "live", "quantity", "pin", "above_cap"])
def test_scope_and_approved_identity_cannot_be_replaced(operator, monkeypatch, change):
    kwargs, provider, _, _, posts, _, _ = operator
    real = controlled.execute_controlled

    def altered(values, portfolio, **deps):
        auth = values.authorization
        if change == "above_cap":
            kwargs["source"].responses["trades"] = [{"price": "60000", "time": millis(NOW)}]
            # All subsequent refreshes use this strictly higher price.
            read = kwargs["source"].read
            kwargs["source"].read = lambda r, p=(): (
                [{"price": "60000", "time": millis(NOW)}] if r == "trades" else read(r, p)
            )
        elif change == "old_cap":
            auth = replace(auth, max_quote_notional=Decimal("5"))
        elif change == "quantity":
            auth = replace(auth, quantity=Decimal("0.00001"))
        elif change == "live":
            object.__setattr__(auth, "environment", "LIVE")
        elif change == "pin":
            values = replace(values, receipt=None)
        return real(replace(values, authorization=auth), portfolio, **deps)

    monkeypatch.setattr(controlled, "execute_controlled", altered)
    result = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert result["status"] == "BLOCKED" and not posts


def _reserve_worker(path, suffix, ready, start, results):
    from pathlib import Path

    ledger = TestnetSubmissionLedger(Path(path))
    ready.put(True)
    start.wait(10)
    try:
        ledger.append(ContentIdentity.from_text(suffix), suffix, SubmissionState.ATTEMPT_STARTED)
        results.put("reserved")
    except AlreadyConsumed:
        results.put("consumed")


def test_two_processes_different_authorizations_share_one_campaign(tmp_path):
    path = tmp_path / "campaign.sqlite"
    TestnetSubmissionLedger.create(path)
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    workers = [
        ctx.Process(target=_reserve_worker, args=(str(path), str(i), ready, start, results))
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    for _ in workers:
        assert ready.get(timeout=20)
    start.set()
    outcomes = sorted(results.get(timeout=20) for _ in workers)
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert outcomes == ["consumed", "reserved"]
    assert TestnetSubmissionLedger(path).inspect_campaign()


@pytest.mark.parametrize("age,allowed", [(9, True), (10, True), (11, False)])
def test_open_orders_permit_inclusive_deadline(first_order, age, allowed):
    from atp.first_testnet_order.execution import SubmissionPermit

    values, deps = first_order
    auth = values.authorization
    permit = SubmissionPermit(
        auth.content_identity,
        ContentIdentity.from_text("request"),
        deps["transport"].source,
        values.readiness.content_identity,
        NOW,
        NOW + timedelta(minutes=1),
        NOW + timedelta(seconds=30),
        NOW + timedelta(minutes=1),
        NOW - timedelta(seconds=age) + timedelta(seconds=10),
        True,
    )
    assert (submission_permit_time(permit, deps["clock"]) is not None) is allowed
    assert (
        submission_permit_time(replace(permit, open_orders_complete=False), deps["clock"]) is None
    )


@pytest.mark.parametrize("change", ["ledger", "authorization", "client", "symbol"])
def test_reconciliation_exact_reservation_binding(first_order, tmp_path, change):
    from atp.first_testnet_order.execution import run_first_order

    values, deps = first_order
    run_first_order(values, **deps, execute=True)
    auth, ledger = values.authorization, deps["ledger"]
    if change == "ledger":
        copy = tmp_path / "copied.sqlite"
        shutil.copyfile(ledger.path, copy)
        ledger = TestnetSubmissionLedger(copy)
    elif change == "authorization":
        auth = replace(auth, max_quote_notional=Decimal("99"))
    payload = dict(
        symbol="OTHER" if change == "symbol" else auth.symbol,
        clientOrderId="wrong" if change == "client" else auth.client_order_id,
        orderId=12,
        side="BUY",
        type="MARKET",
        origQty=str(auth.quantity),
        executedQty="0",
        status="NEW",
        updateTime=millis(NOW),
    )
    result, evidence = reconcile_first_order(auth, ledger, payload, [], NOW)
    assert evidence is None and result.reason_code is not Reason.RECONCILED
    assert deps["ledger"].inspect_campaign()


def test_canonical_missing_ledger_is_never_created(tmp_path, monkeypatch):
    path = tmp_path / "absent.sqlite"
    monkeypatch.setattr(controlled, "OPERATIONAL_LEDGER_PATH", path)
    with pytest.raises(LedgerUnavailable):
        controlled.operational_ledger()
    assert not path.exists()


@pytest.mark.parametrize("fault", ["corrupt", "unwritable", "symlink"])
def test_canonical_ledger_fail_closed(tmp_path, monkeypatch, fault):
    path = tmp_path / "campaign.sqlite"
    TestnetSubmissionLedger.create(path)
    if fault == "corrupt":
        path.write_bytes(b"corrupt")
    elif fault == "unwritable":
        path.chmod(0o400)
    else:
        other = tmp_path / "original.sqlite"
        path.rename(other)
        path.symlink_to(other)
    monkeypatch.setattr(controlled, "OPERATIONAL_LEDGER_PATH", path)
    with pytest.raises(LedgerUnavailable):
        controlled.operational_ledger()


def test_old_five_receipt_cannot_approve_six(operator, monkeypatch):
    from atp.first_testnet_order.trust import trust_first_order
    from tests.first_order_support import SyntheticFirstOrderAuthority

    kwargs, provider, _, _, posts, _, _ = operator
    real = controlled.execute_controlled

    def altered(values, portfolio, **deps):
        old = replace(values.authorization, max_quote_notional=Decimal("5"))
        receipt = trust_first_order(old, SyntheticFirstOrderAuthority(old))
        return real(replace(values, receipt=receipt), portfolio, **deps)

    monkeypatch.setattr(controlled, "execute_controlled", altered)
    report = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert report["status"] == "BLOCKED" and not posts


def test_reconciliation_quote_total_must_match(first_order):
    from atp.first_testnet_order.execution import run_first_order

    values, deps = first_order
    run_first_order(values, **deps, execute=True)
    auth = values.authorization
    payload = dict(
        symbol=auth.symbol,
        clientOrderId=auth.client_order_id,
        orderId=12,
        side="BUY",
        type="MARKET",
        origQty=str(auth.quantity),
        executedQty="0",
        status="NEW",
        updateTime=millis(NOW),
        cummulativeQuoteQty="1",
    )
    result, evidence = reconcile_first_order(auth, deps["ledger"], payload, [], NOW)
    assert result.state is SubmissionState.UNKNOWN and evidence is None


@pytest.mark.parametrize("mismatch", [False, True])
def test_read_only_recovery_is_signed_and_bound_to_campaign(operator, monkeypatch, mismatch):
    from atp.first_testnet_order import preparation_http

    kwargs, provider, _, artifacts, posts, _, _ = operator
    report = preparation_runtime.prepare_operator_execution(**kwargs, credentials=provider)
    assert report["status"] == "ACKNOWLEDGED"
    auth = artifacts["first-order-authorization"]
    reads = []

    def get(resource, query, headers):
        assert "signature=" in query and "X-MBX-APIKEY" in headers
        reads.append(resource)
        if resource == "order":
            return dict(
                symbol=auth.symbol,
                clientOrderId="wrong" if mismatch else auth.client_order_id,
                orderId=12,
                side="BUY",
                type="MARKET",
                origQty=str(auth.quantity),
                executedQty="0",
                status="NEW",
                updateTime=millis(NOW),
                cummulativeQuoteQty="0",
            )
        assert resource == "myTrades"
        return []

    monkeypatch.setattr(preparation_http, "_get", get)
    result, evidence = controlled.reconcile_controlled(auth, credentials=provider, now=lambda: NOW)
    assert reads == ["order", "myTrades"] and len(posts) == 1
    if mismatch:
        assert evidence is None and result.state is SubmissionState.UNKNOWN
    else:
        assert evidence.authorization_identity == auth.content_identity
        assert evidence.submission_ledger_identity == kwargs["ledger"].content_identity
        assert evidence.reservation_identity == kwargs["ledger"].reservation_identity(
            auth.content_identity, auth.client_order_id
        )
        assert evidence.exchange_status == "NEW"
    assert kwargs["ledger"].inspect_campaign()


def test_recovery_source_has_no_economic_route(operator):
    from atp.exchange.read_only import EvidenceError
    from atp.first_testnet_order.preparation_http import ReconciliationReadOnlySource

    _, provider, _, _, posts, _, _ = operator
    source = ReconciliationReadOnlySource(provider)
    for route in ("withdraw", "cancel", "submit", "account", "openOrders", "../order"):
        with pytest.raises(EvidenceError):
            source.read(route)
    assert not posts
