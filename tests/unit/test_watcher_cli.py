"""Bounded interactive prompts and forbidden command modes, without credentials."""

import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from queue import Empty
from threading import Event
from types import SimpleNamespace

import pytest

from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.watcher import WatchWindow
from atp.shared.identity import ContentIdentity
from tests.activation_support import activation  # noqa: F401
from tests.contract.test_signal_watcher import Clock
from tests.unit.test_check_only_http import cli_module
from tests.unit.test_release_deployment import inputs  # noqa: F401
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs"]


@pytest.mark.parametrize("mode", ["--execute", "--prepare"])
def test_watch_never_accepts_economic_or_preparation_mode(mode):
    result = subprocess.run(
        [sys.executable, "scripts/submit_first_testnet_order.py", mode, "--watch"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "watch requires check-only" in result.stderr


def test_prepare_watcher_is_explicit_and_does_not_reuse_watch_modifier(
    monkeypatch, capsys, tmp_path
):
    import json

    module = cli_module()

    def prepared(args, window):
        assert args.watch_prepare_first_order is True
        assert args.check_only is args.prepare is args.execute is False
        assert args.watch is False
        assert window is not None
        return {
            "status": "READY_FOR_CTO_REVIEW",
            "reason_code": "FIRST_ORDER_AUTHORIZATION_REVIEW_REQUIRED",
            "real_economic_calls": 0,
            "LIVE": "LIVE_FORBIDDEN",
        }

    monkeypatch.setattr(module, "run", prepared)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--watch-prepare-first-order",
            "--confirm-testnet-permissions",
            "--source-commit",
            "a" * 40,
            "--release-version",
            "test",
            "--session-dir",
            str(tmp_path / "private"),
        ],
    )
    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "READY_FOR_CTO_REVIEW"
    assert result["transport_call_count"] == result["real_economic_calls"] == 0


def test_prepare_watcher_rejects_first_order_pin_at_cli_boundary():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/submit_first_testnet_order.py",
            "--watch-prepare-first-order",
            "--first-order-authorization-pin",
            "sha256:" + "0" * 64,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "never accepts a first-order authorization pin" in result.stderr


def test_prepare_watcher_inspects_canonical_ledger_without_mutation(
    activation, monkeypatch, tmp_path
):
    module = cli_module()
    canonical = TestnetSubmissionLedger.create(tmp_path / "canonical.sqlite")
    before = canonical.path.read_bytes()
    session = tmp_path / "private"

    class Provider:
        def __init__(self, reference):
            self.reference = reference

        def credentials_present(self):
            return True

    capability = SimpleNamespace(content_identity=ContentIdentity.from_text("capability"))

    class Authority:
        def attest(self, identity):
            return capability

    def release_run(root, version, output, commit, *, on_qualified):
        on_qualified(activation["release"], activation["wheel"])
        return 0

    def collect(output, *, on_qualified):
        on_qualified(activation["tq"], activation["tq_evidence"])
        return 0

    def watch(**kwargs):
        assert kwargs["ledger"] is canonical
        assert kwargs["prepare_first_order"] is True
        assert canonical.inspect_campaign() is False
        return {
            "status": "READY_FOR_CTO_REVIEW",
            "reason_code": "FIRST_ORDER_AUTHORIZATION_REVIEW_REQUIRED",
            "real_economic_calls": 0,
            "transport_call_count": 0,
            "LIVE": "LIVE_FORBIDDEN",
        }

    monkeypatch.setattr(module, "operational_ledger", lambda: canonical)
    monkeypatch.setattr(module, "ReferencedEnvironmentCredentialsProvider", Provider)
    monkeypatch.setattr(module, "RuntimeCredentialCapabilityAuthority", lambda *args: Authority())
    monkeypatch.setattr(module, "TrustedCredentialPermissionAuthority", lambda *args: object())
    monkeypatch.setattr(module, "inspect_source", lambda root: activation["release"].source)
    monkeypatch.setattr(module, "watch_check_only", watch)
    monkeypatch.setattr(
        module,
        "pre_watch_feasibility",
        lambda source=None: {
            "status": "FEASIBLE",
            "feasibility_identity": "synthetic-feasibility",
            "real_economic_calls": 0,
            "LIVE": "LIVE_FORBIDDEN",
        },
    )
    monkeypatch.setitem(sys.modules, "release", SimpleNamespace(run=release_run))
    monkeypatch.setitem(sys.modules, "qualify_testnet", SimpleNamespace(collect=collect))

    args = SimpleNamespace(
        check_only=False,
        prepare=False,
        execute=False,
        watch_prepare_first_order=True,
        confirm_testnet_permissions=True,
        session_dir=session,
        release_version="008d-test",
        source_commit="a" * 40,
        activation_grant_pin=None,
        first_order_authorization_pin=None,
        request_trust_pins=False,
    )
    result = module.run(args, WatchWindow(lambda: NOW, Event(), lambda seconds: False))

    assert result["status"] == "READY_FOR_CTO_REVIEW"
    assert canonical.path.read_bytes() == before
    assert canonical.inspect_campaign() is False
    assert not (session / "submission.sqlite").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_pin_prompt_can_expire_or_stop_without_operator_input(activation, monkeypatch, cancel):
    module = cli_module()
    clock, stop = Clock(), Event()
    window = WatchWindow(clock.now, stop, clock.wait)
    grant = replace(
        activation["grant"], validity_start=NOW, validity_end=NOW + timedelta(minutes=30)
    )
    window.bind(grant)

    class WaitingQueue:
        def get(self, timeout):
            if cancel:
                stop.set()
            else:
                clock.at = grant.validity_end
            raise Empty

    class NoTerminalThread:
        def __init__(self, **kwargs):
            assert kwargs["daemon"] is True

        def start(self):
            pass

    monkeypatch.setattr(module, "Queue", WaitingQueue)
    monkeypatch.setattr(module, "Thread", NoTerminalThread)
    with pytest.raises(
        EvidenceError, match="WATCHER_STOPPED" if cancel else "ACTIVATION_GRANT_EXPIRED"
    ):
        module.bounded_input("external pin: ", window)


def test_pin_entered_after_expiry_is_rejected(activation, monkeypatch):
    module = cli_module()
    clock = Clock()
    window = WatchWindow(clock.now, Event(), clock.wait)
    grant = replace(
        activation["grant"], validity_start=NOW, validity_end=NOW + timedelta(minutes=30)
    )
    window.bind(grant)

    def delayed_input(prompt):
        clock.at = grant.validity_end
        return str(grant.content_identity)

    monkeypatch.setattr("builtins.input", delayed_input)
    with pytest.raises(EvidenceError, match="ACTIVATION_GRANT_EXPIRED"):
        module.bounded_input("external pin: ", window)


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
def test_cli_signals_stop_and_restore_handlers(monkeypatch, capsys, signal_name):
    import json
    import signal

    module = cli_module()
    sig = getattr(signal, signal_name)
    previous = signal.getsignal(sig)

    def interrupted(args, window):
        signal.raise_signal(sig)
        window.read()
        pytest.fail("Stop signal must prevent continuation")

    monkeypatch.setattr(module, "run", interrupted)
    monkeypatch.setattr(sys, "argv", ["check", "--check-only", "--watch"])
    assert module.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["reason_code"] == "WATCHER_STOPPED"
    assert result["real_economic_calls"] == result["transport_call_count"] == 0
    assert signal.getsignal(sig) == previous
