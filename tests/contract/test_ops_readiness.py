import ast
from pathlib import Path

from atp.ops import OperationalReasonCode, ReadinessStatus, observability_evidence, readiness
from tests.unit.test_ops import config, qualification


def test_qualification_observability_ops_contract_without_trading(tmp_path):
    (tmp_path / "artifacts").mkdir()
    proof = qualification()
    obs = observability_evidence()
    ready = readiness(config(tmp_path, "BACKTEST"), qualification=proof, observability=obs)
    assert ready.readiness_status is ReadinessStatus.READY
    assert ready.qualification_result_identity == proof.content_identity
    assert ready.observability_evidence_identity == obs.content_identity
    live = readiness(config(tmp_path, "LIVE"), qualification=proof, observability=obs)
    assert live.readiness_status is ReadinessStatus.BLOCKED
    assert live.reason_code is OperationalReasonCode.LIVE_FORBIDDEN


def test_ops_has_no_business_network_or_enable_authority():
    forbidden = (
        "atp.oms",
        "atp.exchange",
        "atp.accounting",
        "atp.risk",
        "atp.strategy",
        "atp.backtesting",
        "openai",
        "socket",
        "requests",
        "httpx",
        "subprocess",
    )
    for path in Path("src/atp/ops").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith(forbidden) for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden)
            if isinstance(node, ast.FunctionDef):
                assert node.name not in (
                    "enable_live",
                    "enable_testnet",
                    "withdraw",
                    "create_order",
                    "create_fill",
                )
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert name not in (
                    "now",
                    "utcnow",
                    "evaluate_suite",
                    "evaluate_case",
                    "place_order",
                    "replay",
                )
