"""Synthetic time and GET fixtures exercise the real Strategy/Risk/check-only pipeline."""

from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest

from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.model import SubmissionState
from atp.first_testnet_order.preparation_runtime import CheckOnlyTransport
from atp.first_testnet_order.watcher import WatchWindow, watch_check_only
from atp.shared.identity import ContentIdentity
from tests.activation_support import activation  # noqa: F401
from tests.contract.test_check_only_preparation import OfflineSource, candles, millis
from tests.unit.test_release_deployment import inputs  # noqa: F401
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs"]


class Clock:
    def __init__(self, at=NOW):
        self.at = at
        self.sleeps = []

    def now(self):
        return self.at

    def wait(self, seconds):
        self.sleeps.append(seconds)
        self.at += timedelta(seconds=seconds)
        return False


class Source(OfflineSource):
    def __init__(self, clock, signals):
        super().__init__()
        self.clock, self.signals = clock, iter(signals)
        self.evaluations = []

    def read(self, resource, parameters=()):
        at = self.clock.now()
        if resource == "time":
            self.responses[resource] = {"serverTime": millis(at)}
        elif resource == "klines":
            self.evaluations.append(at)
            rows = candles(next(self.signals, "NO_ACTION"))
            close = at.replace(second=0, microsecond=0) - timedelta(minutes=at.minute % 5)
            delta = millis(close) - millis(NOW)
            for row in rows:
                row[0] += delta
                row[6] += delta
            self.responses[resource] = rows
        elif resource == "trades":
            self.responses[resource] = [{"price": "50000", "time": millis(at)}]
        return super().read(resource, parameters)


@pytest.fixture(autouse=True)
def no_network_or_economic_calls(monkeypatch):
    import socket

    from atp.exchange.transport import BinanceTestnetHTTPTransport

    def forbidden(*args, **kwargs):
        pytest.fail("Network/economic transport must never be invoked")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(CheckOnlyTransport, "submit", forbidden)
    monkeypatch.setattr(BinanceTestnetHTTPTransport, "perform", forbidden)


def harness(
    activation,
    tmp_path,
    signals=("NO_ACTION",),
    *,
    at=NOW,
    grant=None,
    pin_mode="exact",
    second_mode="exact",
    prepare_first_order=False,
    after_pin=None,
    after_report=None,
    stop_after=None,
    grant_duration=timedelta(minutes=30),
):
    clock = Clock(at)
    stop = Event()
    window = WatchWindow(clock.now, stop, clock.wait)
    source = Source(clock, signals)
    artifacts, prompts, reports = {}, [], []
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    ledger = TestnetSubmissionLedger.create(tmp_path / "watch.sqlite")

    def save(kind, artifact):
        assert kind not in artifacts, "No silent artifact overwrite/renewal"
        artifacts[kind] = artifact

    def pin(kind):
        prompts.append(kind)
        mode = pin_mode if kind == "activation-grant" else second_mode
        if after_pin:
            after_pin(kind, clock, window)
        # Explicit synthetic operator approval, confined to tests.
        if mode == "missing":
            return None
        if mode == "wrong":
            return ContentIdentity.from_text("unapproved")
        return artifacts[kind].content_identity

    def report(result):
        reports.append(result)
        if after_report is not None:
            after_report(result, ledger, source)
        if stop_after is not None and len(reports) >= stop_after:
            stop.set()

    result = watch_check_only(
        source=source,
        window=window,
        release=activation["release"],
        wheel=activation["wheel"],
        tq=activation["tq"],
        tq_evidence=activation["tq_evidence"],
        credential_source_identity=activation["credential_source_identity"],
        credential_authority=activation["credential_authority"],
        workspace=tmp_path,
        ledger=ledger,
        artifact_sink=save,
        trust_pin_source=pin,
        report_sink=report,
        source_check=lambda: None,
        activation_grant=grant,
        prepare_first_order=prepare_first_order,
        activation_grant_duration=grant_duration,
    )
    assert result["real_economic_calls"] == 0
    assert result["transport_call_count"] == 0
    assert result["LIVE"] == "LIVE_FORBIDDEN"
    return result, source, artifacts, prompts, reports, clock


@pytest.mark.parametrize("mode", ["missing", "wrong"])
@pytest.mark.parametrize("prepare_first_order", [False, True])
def test_external_pin_required_before_strategy(activation, tmp_path, mode, prepare_first_order):
    result, source, _, prompts, _, _ = harness(
        activation,
        tmp_path,
        pin_mode=mode,
        prepare_first_order=prepare_first_order,
    )
    assert result["reason_code"] == "TRUST_PIN_REQUIRED"
    assert source.evaluations == []
    assert prompts == ["activation-grant"]
    assert all(name == "time" for name, _ in source.calls)


def test_repeated_no_action_exit_retains_one_grant_and_expires(activation, tmp_path):
    result, source, artifacts, prompts, reports, clock = harness(
        activation, tmp_path, signals=("NO_ACTION", "EXIT", "NO_ACTION")
    )
    grant = artifacts["activation-grant"]
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert clock.at == grant.validity_end
    assert prompts == ["activation-grant"]
    assert len(reports) == 6
    assert all(r["status"] == "NOT_READY" and r["real_economic_calls"] == 0 for r in reports)
    assert reports[1]["strategy_signal"] == "EXIT"
    assert source.evaluations == [NOW + timedelta(minutes=5 * n) for n in range(6)]
    assert "first-order-authorization" not in artifacts


def test_preparation_watcher_grant_expiry_stops_without_second_pin(activation, tmp_path):
    result, _, artifacts, prompts, _, clock = harness(
        activation,
        tmp_path,
        signals=("NO_ACTION", "EXIT"),
        prepare_first_order=True,
    )
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert clock.at == artifacts["activation-grant"].validity_end
    assert artifacts["activation-grant"].validity_end - artifacts[
        "activation-grant"
    ].validity_start == timedelta(minutes=30)
    assert prompts == ["activation-grant"]
    assert "first-order-authorization" not in artifacts


def test_long_preparation_watch_retains_one_grant_context_and_reference(
    activation, monkeypatch, tmp_path
):
    import atp.first_testnet_order.preparation_runtime as preparation_runtime

    duration = timedelta(minutes=720)
    context_identities = []
    original_validate = preparation_runtime.validate_activation

    def record_context(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        if result.context is not None:
            context_identities.append(result.context.content_identity)
        return result

    monkeypatch.setattr(preparation_runtime, "validate_activation", record_context)
    result, source, artifacts, prompts, reports, clock = harness(
        activation,
        tmp_path,
        prepare_first_order=True,
        grant_duration=duration,
    )

    grant = artifacts["activation-grant"]
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert grant.validity_end - grant.validity_start == duration
    assert clock.at == grant.validity_end
    assert prompts == ["activation-grant"]
    assert len(reports) == len(source.evaluations) == 144
    assert len(context_identities) == 144
    assert len(set(context_identities)) == 1
    assert "first-order-authorization" not in artifacts


def test_preparation_grant_end_is_capped_by_credential_permission(activation, tmp_path):
    permission_end = NOW + timedelta(minutes=75)

    class ShortCapability(type(activation["credential_authority"])):
        def attest(self, source):
            return replace(super().attest(source), permission_valid_until=permission_end)

    scoped = activation | {"credential_authority": ShortCapability()}
    result, _, artifacts, prompts, _, clock = harness(
        scoped,
        tmp_path,
        prepare_first_order=True,
        grant_duration=timedelta(minutes=720),
    )

    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert artifacts["activation-grant"].validity_end == permission_end
    assert clock.at == permission_end
    assert prompts == ["activation-grant"]


def test_effective_grant_validity_changes_candidate_identity(activation, tmp_path):
    _, _, short, _, _, _ = harness(
        activation,
        tmp_path / "short",
        pin_mode="missing",
        prepare_first_order=True,
        grant_duration=timedelta(minutes=30),
    )
    _, _, long, _, _, _ = harness(
        activation,
        tmp_path / "long",
        pin_mode="missing",
        prepare_first_order=True,
        grant_duration=timedelta(minutes=720),
    )

    assert short["activation-grant"].validity_end == NOW + timedelta(minutes=30)
    assert long["activation-grant"].validity_end == NOW + timedelta(minutes=720)
    assert short["activation-grant"].content_identity != long["activation-grant"].content_identity


def test_late_start_uses_remaining_lifetime(activation, tmp_path):
    grant = replace(
        activation["grant"], validity_start=NOW, validity_end=NOW + timedelta(minutes=30)
    )
    result, source, _, _, _, clock = harness(
        activation, tmp_path, at=NOW + timedelta(minutes=8), grant=grant
    )
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert clock.at == NOW + timedelta(minutes=30)
    assert source.evaluations == [NOW + timedelta(minutes=m) for m in (8, 10, 15, 20, 25)]


def test_expired_start_performs_no_read_or_evaluation(activation, tmp_path):
    grant = replace(
        activation["grant"], validity_start=NOW - timedelta(minutes=30), validity_end=NOW
    )
    result, source, _, prompts, _, _ = harness(activation, tmp_path, grant=grant)
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert source.calls == prompts == []


@pytest.mark.parametrize(
    "second_mode,expected",
    [("exact", "READY_TO_SUBMIT"), ("wrong", "BLOCKED"), ("missing", "BLOCKED")],
)
def test_real_crossover_runs_gates_and_requires_second_pin(
    activation, tmp_path, second_mode, expected
):
    result, source, artifacts, prompts, reports, _ = harness(
        activation, tmp_path, signals=("NO_ACTION", "LONG_ENTRY"), second_mode=second_mode
    )
    assert result["status"] == expected, str(result)
    assert prompts == ["activation-grant", "first-order-authorization"]
    assert len(reports) == 1
    assert {"account", "openOrders", "exchangeInfo", "trades"} <= {n for n, _ in source.calls}
    auth = artifacts["first-order-authorization"]
    assert auth.activation_grant_identity == artifacts["activation-grant"].content_identity
    if expected == "READY_TO_SUBMIT":
        assert result["ledger_state"] == "NOT_ATTEMPTED"
    else:
        assert result["reason_code"] == "TRUST_PIN_REQUIRED"


def test_preparation_watcher_stops_at_candidate_without_second_pin(
    activation, tmp_path, monkeypatch
):
    from atp.first_testnet_order import controlled, preparation_runtime

    monkeypatch.setattr(
        preparation_runtime,
        "trust_first_order",
        lambda *args, **kwargs: pytest.fail("First-order trust receipt must not be created"),
    )
    monkeypatch.setattr(
        controlled,
        "execute_controlled",
        lambda *args, **kwargs: pytest.fail("Economic execution must not be invoked"),
    )
    result, _, artifacts, prompts, reports, _ = harness(
        activation,
        tmp_path,
        signals=("NO_ACTION", "LONG_ENTRY"),
        prepare_first_order=True,
    )
    authorization = artifacts["first-order-authorization"]
    context = next(
        artifact
        for name, artifact in artifacts.items()
        if name.endswith("runtime-authorization-context")
    )
    grant = artifacts["activation-grant"]

    assert result["status"] == "READY_FOR_CTO_REVIEW"
    assert result["reason_code"] == "FIRST_ORDER_AUTHORIZATION_REVIEW_REQUIRED"
    assert result["first_order_identity"] == str(authorization.content_identity)
    assert result["ledger_state"] == "NOT_ATTEMPTED"
    assert result["transport_call_count"] == result["real_economic_calls"] == 0
    assert prompts == ["activation-grant"]
    assert len(reports) == 1 and reports[0]["strategy_signal"] == "NO_ACTION"
    assert authorization.activation_grant_identity == grant.content_identity
    assert authorization.runtime_authorization_context_identity == context.content_identity
    assert context.credential_source_identity == activation["credential_source_identity"]
    assert {
        "quantity-selection",
        "open-orders-evidence",
        "portfolio-evidence",
        "upstream-proof",
        "strategy-evaluation",
    } <= {name.split("-", 2)[-1] for name in artifacts}
    assert TestnetSubmissionLedger(tmp_path / "watch.sqlite").inspect_campaign() is False


def test_preparation_watcher_detects_external_campaign_consumption_before_next_tick(
    activation, tmp_path, monkeypatch
):
    from atp.first_testnet_order import controlled, preparation_runtime

    monkeypatch.setattr(
        preparation_runtime,
        "trust_first_order",
        lambda *args, **kwargs: pytest.fail("First-order trust receipt must not be created"),
    )
    monkeypatch.setattr(
        controlled,
        "execute_controlled",
        lambda *args, **kwargs: pytest.fail("Economic execution must not be invoked"),
    )
    externally_consumed_bytes = []
    calls_at_consumption = []

    def consume_after_first_tick(result, ledger, source):
        assert result["strategy_signal"] == "NO_ACTION"
        ledger.append(
            ContentIdentity.from_text("external-authorization"),
            "external-client-order-id",
            SubmissionState.ATTEMPT_STARTED,
        )
        externally_consumed_bytes.append(ledger.path.read_bytes())
        calls_at_consumption.append(tuple(source.calls))

    result, source, artifacts, prompts, reports, _ = harness(
        activation,
        tmp_path,
        signals=("NO_ACTION", "LONG_ENTRY"),
        prepare_first_order=True,
        after_report=consume_after_first_tick,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "FIRST_ORDER_ALREADY_CONSUMED"
    assert result["transport_call_count"] == result["real_economic_calls"] == 0
    assert result["LIVE"] == "LIVE_FORBIDDEN"
    assert len(reports) == 1
    assert len(source.evaluations) == 1
    assert tuple(source.calls) == calls_at_consumption[0]
    assert prompts == ["activation-grant"]
    assert "first-order-authorization" not in artifacts
    assert (tmp_path / "watch.sqlite").read_bytes() == externally_consumed_bytes[0]
    assert TestnetSubmissionLedger(tmp_path / "watch.sqlite").inspect_campaign() is True


@pytest.mark.parametrize("kind", ["activation-grant", "first-order-authorization"])
def test_expiry_during_pin_wait_never_continues(activation, tmp_path, kind):
    def delay(prompt, clock, window):
        if prompt == kind:
            clock.at = window.grant.validity_end

    result, source, _, _, _, _ = harness(
        activation, tmp_path, signals=("LONG_ENTRY",), after_pin=delay
    )
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    if kind == "activation-grant":
        assert source.evaluations == []


def test_stale_price_after_second_pin_blocks(activation, tmp_path):
    def delay(kind, clock, window):
        if kind == "first-order-authorization":
            clock.at += timedelta(seconds=11)

    result, _, _, prompts, _, _ = harness(
        activation, tmp_path, signals=("LONG_ENTRY",), after_pin=delay
    )
    assert result["reason_code"] == "PRICE_EVIDENCE_STALE"
    assert len(prompts) == 2


def test_operator_stop_prevents_next_tick(activation, tmp_path):
    result, source, _, _, _, clock = harness(activation, tmp_path, stop_after=1)
    assert result["reason_code"] == "WATCHER_STOPPED"
    assert len(source.evaluations) == 1
    assert clock.sleeps == []


def test_clock_rollback_is_fail_closed():
    clock = Clock()
    window = WatchWindow(clock.now, Event(), clock.wait)
    window.read()
    clock.at -= timedelta(seconds=1)
    with pytest.raises(EvidenceError, match="TIME_EVIDENCE_INVALID"):
        window.read()


def test_stop_while_waiting_does_not_start_scheduled_tick():
    clock, stop = Clock(), Event()
    window = WatchWindow(clock.now, stop)
    # No grant construction or authority needed to test cancellation of the wait primitive.
    from types import SimpleNamespace

    window.grant = SimpleNamespace(validity_start=NOW, validity_end=NOW + timedelta(minutes=30))

    def stop_on_wait(seconds):
        stop.set()
        return True

    window.wait = stop_on_wait
    with pytest.raises(EvidenceError, match="WATCHER_STOPPED"):
        window.next_close()
    assert clock.at == NOW


def test_in_flight_read_expiring_is_discarded(activation):
    from atp.first_testnet_order.watcher import BoundedReadOnlySource

    clock = Clock()
    window = WatchWindow(clock.now, Event(), clock.wait)
    grant = replace(
        activation["grant"], validity_start=NOW, validity_end=NOW + timedelta(minutes=30)
    )
    window.bind(grant)
    calls = []

    class DelayedSource:
        def read(self, resource, parameters=()):
            calls.append(resource)
            clock.at = grant.validity_end
            return {"serverTime": millis(clock.at)}

    source = BoundedReadOnlySource(DelayedSource(), window)
    with pytest.raises(EvidenceError, match="ACTIVATION_GRANT_EXPIRED"):
        source.read("time")
    with pytest.raises(EvidenceError, match="ACTIVATION_GRANT_EXPIRED"):
        source.read("time")
    assert calls == ["time"]


def test_existing_exposure_does_not_request_second_pin(activation, tmp_path, monkeypatch):
    original = Source.read

    def exposed(self, resource, parameters=()):
        if resource == "account":
            self.responses[resource]["balances"][0]["free"] = "0.001"
        return original(self, resource, parameters)

    monkeypatch.setattr(Source, "read", exposed)
    result, _, artifacts, prompts, _, _ = harness(activation, tmp_path, signals=("LONG_ENTRY",))
    assert result["status"] == "BLOCKED"
    assert result["risk_status"] != "APPROVED"
    assert prompts == ["activation-grant"]
    assert "first-order-authorization" not in artifacts


@pytest.mark.parametrize("prepare_first_order", [False, True])
def test_stale_price_before_candidate_never_requests_pin(
    activation, tmp_path, monkeypatch, prepare_first_order
):
    original = Source.read

    def stale(self, resource, parameters=()):
        result = original(self, resource, parameters)
        if resource == "trades":
            result[0]["time"] = millis(self.clock.at - timedelta(seconds=11))
        return result

    monkeypatch.setattr(Source, "read", stale)
    result, _, artifacts, prompts, _, _ = harness(
        activation,
        tmp_path,
        signals=("LONG_ENTRY",),
        prepare_first_order=prepare_first_order,
    )
    assert result["reason_code"] == "PRICE_EVIDENCE_STALE"
    assert prompts == ["activation-grant"]
    assert "first-order-authorization" not in artifacts


@pytest.mark.parametrize("prepare_first_order", [False, True])
def test_live_grant_never_evaluates_strategy(activation, tmp_path, prepare_first_order):
    grant = replace(
        activation["grant"], validity_start=NOW, validity_end=NOW + timedelta(minutes=30)
    )
    object.__setattr__(grant, "allowed_environment", "LIVE")
    with pytest.raises(EvidenceError, match="LIVE_FORBIDDEN"):
        harness(
            activation,
            tmp_path,
            grant=grant,
            prepare_first_order=prepare_first_order,
        )


def test_expiry_between_scheduled_closes_cancels_next_tick(activation, tmp_path):
    result, source, artifacts, _, _, clock = harness(
        activation, tmp_path, at=NOW + timedelta(minutes=2)
    )
    assert result["reason_code"] == "ACTIVATION_GRANT_EXPIRED"
    assert clock.at == artifacts["activation-grant"].validity_end == NOW + timedelta(minutes=32)
    assert source.evaluations[-1] == NOW + timedelta(minutes=30)
    assert len(source.evaluations) == 7


def test_grant_cannot_exceed_thirty_minutes(activation, tmp_path):
    grant = replace(
        activation["grant"], validity_start=NOW, validity_end=NOW + timedelta(minutes=31)
    )
    with pytest.raises(EvidenceError, match="ACTIVATION_GRANT_INVALID"):
        harness(activation, tmp_path, grant=grant)
