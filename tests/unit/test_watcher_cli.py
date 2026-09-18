"""Bounded interactive prompts and forbidden command modes, without credentials."""

import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from queue import Empty
from threading import Event

import pytest

from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.watcher import WatchWindow
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
