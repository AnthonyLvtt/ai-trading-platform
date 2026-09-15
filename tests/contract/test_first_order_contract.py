"""Full synthetic source/qualification/release/Risk/OPS chain; no economic network."""

import ast
import json
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from atp.first_testnet_order.execution import run_first_order
from atp.first_testnet_order.model import Reason, SubmissionState
from atp.first_testnet_order.reconciliation import reconcile_first_order
from tests.activation_support import activation
from tests.first_order_support import first_order
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs", "first_order"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No real network in first-order contracts")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize(
    ("status", "quantity"),
    [
        ("FILLED", "0.001"),
        ("PARTIALLY_FILLED", "0.0005"),
        ("NEW", "0"),
        ("CANCELED", "0"),
        ("EXPIRED", "0"),
    ],
)
def test_full_chain_reconciled_execution_without_accounting(first_order, status, quantity):
    from atp.accounting import AccountingEngine

    # Existing Accounting remains immutable and uses its accepted simulation contract.
    from tests.unit.test_accounting_engine import accounting_input

    accounting = AccountingEngine().replay(accounting_input())
    before = repr(accounting)
    values, deps = first_order
    assert run_first_order(values, **deps).reason_code is Reason.READY_TO_SUBMIT
    assert deps["transport"].calls == 0
    assert run_first_order(values, **deps, execute=True).state is SubmissionState.ACKNOWLEDGED
    auth = values.authorization
    at = int(NOW.timestamp() * 1000)
    order = dict(
        symbol=auth.symbol,
        clientOrderId=auth.client_order_id,
        orderId=12,
        side="BUY",
        type="MARKET",
        origQty="0.001",
        executedQty=quantity,
        status=status,
        updateTime=at,
    )
    fills = (
        []
        if quantity == "0"
        else [
            dict(
                id=3,
                orderId=12,
                symbol=auth.symbol,
                price="50000",
                qty=quantity,
                quoteQty=str(Decimal(quantity) * 50000),
                time=at,
            )
        ]
    )
    result, evidence = reconcile_first_order(auth, deps["ledger"], order, fills, NOW)
    assert result.state is SubmissionState.RECONCILED
    assert evidence.exchange_status == status
    assert evidence.cumulative_base_quantity == Decimal(quantity)
    assert evidence == reconcile_first_order(auth, deps["ledger"], order, fills, NOW)[1]
    assert repr(accounting) == before
    assert deps["transport"].calls == 1
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.FIRST_ORDER_ALREADY_CONSUMED
    )


def test_unknown_reconciliation_never_allows_retry(first_order):
    values, deps = first_order
    deps["transport"].reply = TimeoutError()
    run_first_order(values, **deps, execute=True)
    result, evidence = reconcile_first_order(
        values.authorization, deps["ledger"], {"code": -2013, "msg": "not found"}, [], NOW
    )
    assert result.state is SubmissionState.UNKNOWN and evidence is None
    assert (
        run_first_order(values, **deps, execute=True).reason_code
        is Reason.FIRST_ORDER_ALREADY_CONSUMED
    )
    assert deps["transport"].calls == 1


def test_normal_cli_and_ci_remain_blocked():
    for mode in ("--check-only", "--execute"):
        result = subprocess.run(
            [sys.executable, "scripts/submit_first_testnet_order.py", mode],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert json.loads(result.stdout) == dict(
            status="BLOCKED",
            transport_call_count=0,
            reason_code="FIRST_ORDER_AUTHORIZATION_REQUIRED",
            real_economic_calls=0,
            LIVE="LIVE_FORBIDDEN",
        )
    result = subprocess.run(
        [sys.executable, "scripts/submit_first_testnet_order.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    for workflow in Path(".github/workflows").glob("*.yml"):
        assert "submit_first_testnet_order" not in workflow.read_text()


def test_authority_boundaries():
    for path in Path("src/atp/first_testnet_order").glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        assert not any(
            name.startswith(("atp.accounting", "atp.oms", "atp.ai", "atp.ml")) for name in imports
        )
        assert not any(
            isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and n.name in ("withdraw", "cancel", "transfer", "enable_live", "enable_testnet")
            for n in ast.walk(tree)
        )
    script = Path("scripts/submit_first_testnet_order.py").read_text()
    # ENG-TO-OPS-002 explicitly snapshots then removes credentials before build/test children.
    assert "ReferencedEnvironmentCredentialsProvider(reference)" in script
    assert "os.environ.pop(name, None)" in script
    assert "Synthetic" not in script
