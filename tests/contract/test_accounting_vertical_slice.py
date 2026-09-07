from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atp.accounting import (
    AccountingEngine,
    AccountingExecution,
    AccountingReplayInput,
    AccountingStatus,
)
from atp.backtesting import ExecutionProvenance, SimulatedFill, SimulatedOrder, SimulatedOrderSide
from atp.backtesting.identity import SimulationPolicyId
from atp.data.identity import DatasetId, SnapshotId
from atp.risk.identity import RiskDecisionId
from atp.risk.model import RiskReasonCode, RiskStatus
from atp.shared.identity import ContentIdentity
from atp.strategy.identity import StrategyDecisionId, StrategyEvaluationId

START = datetime(2026, 1, 1, tzinfo=UTC)


def simulated_fill(*, side: SimulatedOrderSide, minute: int, price: str) -> SimulatedFill:
    token = ContentIdentity.from_text(f"contract:{side.value}:{minute}")
    provenance = ExecutionProvenance(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:accounting-contract:v1"),
        snapshot_identity=ContentIdentity.from_text("snapshot"),
        evaluation_bar_identity=ContentIdentity.from_text(f"bar:{minute - 1}"),
        evaluation_bar_event_time=START + timedelta(minutes=minute - 1),
        evaluation_time=START + timedelta(minutes=minute - 1),
        strategy_evaluation_id=StrategyEvaluationId(f"evaluation:{minute}"),
        strategy_evaluation_identity=token,
        strategy_decision_id=StrategyDecisionId(f"decision:{minute}"),
        strategy_signal_identity=token,
        risk_decision_id=RiskDecisionId(f"risk:{minute}"),
        risk_decision_identity=token,
        risk_status=RiskStatus.APPROVED,
        risk_reason_code=RiskReasonCode.POLICY_COMPLIANT,
        simulation_policy_id=SimulationPolicyId("ATP_SIM_EXEC_V1"),
        simulation_policy_version="1.0",
        simulation_policy_identity=ContentIdentity.from_text("simulation-policy"),
        symbol="BTCUSDT",
    )
    order = SimulatedOrder.create(
        side=side,
        created_at=START + timedelta(minutes=minute - 1),
        provenance=provenance,
    )
    return SimulatedFill.create(
        order=order,
        fill_price=Decimal(price),
        fill_time=START + timedelta(minutes=minute),
        source_bar_identity=ContentIdentity.from_text(f"fill-bar:{minute}"),
        source_bar_event_time=START + timedelta(minutes=minute),
    )


def test_simulated_fill_to_accounting_result_vertical_slice() -> None:
    entry = simulated_fill(side=SimulatedOrderSide.BUY_ENTRY, minute=1, price="4.5")
    exit_fill = simulated_fill(side=SimulatedOrderSide.SELL_EXIT, minute=2, price="0.5")

    result = AccountingEngine().replay(
        AccountingReplayInput(
            initial_cash=Decimal("100"),
            currency="USDT",
            executions=(
                AccountingExecution(entry, Decimal("2")),
                AccountingExecution(exit_fill, Decimal("2")),
            ),
        )
    )

    assert result.status is AccountingStatus.COMPLETED
    assert len(result.ledger) == 2
    assert result.final_state.cash == Decimal("92.0")
    assert result.final_state.cumulative_realized_pnl == Decimal("-8.0")
    assert result.ledger[0].provenance.simulated_fill_identity == entry.content_identity
    assert result.ledger[1].provenance.simulated_fill_identity == exit_fill.content_identity


def test_accounting_has_no_forbidden_authority_imports() -> None:
    root = Path("src/atp/accounting")
    forbidden = ("atp.oms", "atp.exchange", "atp.persistence", "atp.observability")

    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        imports.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(name.startswith(forbidden) for name in imports)


def test_accounting_models_expose_no_order_or_fill_factory() -> None:
    import atp.accounting as accounting

    assert not hasattr(accounting, "SimulatedOrder")
    assert not hasattr(accounting, "SimulatedFill")
