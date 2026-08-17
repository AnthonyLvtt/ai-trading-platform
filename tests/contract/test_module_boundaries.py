from __future__ import annotations

import importlib

import pytest

MODULES = [
    "atp.data",
    "atp.strategy",
    "atp.risk",
    "atp.oms",
    "atp.accounting",
    "atp.backtesting",
    "atp.observability",
    "atp.qualification",
    "atp.ops",
    "atp.web",
    "atp.exchange",
    "atp.release",
    "atp.persistence",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_import_has_no_exchange_side_effect(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
