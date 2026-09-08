"""Executable security and authority probes for qualification V1."""

import ast
from pathlib import Path


def test_no_withdrawal_or_live_activation_capability():
    for path in Path("src/atp").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                assert not node.name.lower().startswith(
                    ("withdraw", "enable_live", "activate_live")
                )


def test_qualification_has_no_business_or_network_calls():
    forbidden = ("atp.oms", "atp.exchange", "socket", "requests", "httpx", "subprocess", "openai")
    for path in Path("src/atp/test_qualification").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden)
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(forbidden) for alias in node.names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in (
                    "now",
                    "utcnow",
                    "place_order",
                    "withdraw",
                    "replay",
                    "value",
                )
