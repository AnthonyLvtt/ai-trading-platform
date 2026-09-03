from __future__ import annotations

import importlib
import socket
import sys
from collections.abc import Callable
from typing import NoReturn

import pytest

SHARED_MODULES = [
    "atp.shared",
    "atp.shared.config",
    "atp.shared.environment",
    "atp.shared.errors",
    "atp.shared.identity",
    "atp.shared.result",
    "atp.shared.serialization",
    "atp.shared.time",
]

DATA_MODULES = [
    "atp.data",
    "atp.data.backfill",
    "atp.data.consumption",
    "atp.data.identity",
    "atp.data.lineage",
    "atp.data.snapshot",
    "atp.data.temporal",
    "atp.data.universe",
]

STRATEGY_MODULES = [
    "atp.strategy",
    "atp.strategy.identity",
    "atp.strategy.model",
    "atp.strategy.sma",
]

RISK_MODULES = [
    "atp.risk",
    "atp.risk.engine",
    "atp.risk.identity",
    "atp.risk.model",
    "atp.risk.policy",
]

BACKTESTING_MODULES = [
    "atp.backtesting",
    "atp.backtesting.engine",
    "atp.backtesting.identity",
    "atp.backtesting.model",
    "atp.backtesting.policy",
]

MODULES = [
    *SHARED_MODULES,
    *DATA_MODULES,
    *STRATEGY_MODULES,
    *RISK_MODULES,
    *BACKTESTING_MODULES,
    "atp.oms",
    "atp.accounting",
    "atp.observability",
    "atp.test_qualification",
    "atp.ops",
    "atp.web",
    "atp.exchange",
    "atp.release_deployment",
    "atp.persistence",
]


def _block_network(*args: object, **kwargs: object) -> NoReturn:
    del args, kwargs
    raise AssertionError("network access is forbidden while importing ATP modules")


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> Callable[..., NoReturn]:
    monkeypatch.setattr(socket, "create_connection", _block_network)
    monkeypatch.setattr(socket, "getaddrinfo", _block_network)
    monkeypatch.setattr(socket.socket, "connect", _block_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _block_network)
    return _block_network


def test_network_guard_rejects_connection_attempt(
    block_network: Callable[..., NoReturn],
) -> None:
    del block_network
    with pytest.raises(AssertionError, match="network access is forbidden"):
        socket.create_connection(("exchange.invalid", 443))


@pytest.mark.parametrize("module_name", MODULES)
def test_module_import_has_no_exchange_side_effect(
    module_name: str,
    block_network: Callable[..., NoReturn],
) -> None:
    del block_network
    sys.modules.pop(module_name, None)
    assert importlib.import_module(module_name) is not None
