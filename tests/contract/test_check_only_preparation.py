"""Real baseline/Risk and gates, deterministic read-only Exchange fixture responses."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.preparation_runtime import prepare_check_only
from tests.activation_support import activation  # noqa: F401
from tests.unit.test_release_deployment import inputs  # noqa: F401
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs"]


def millis(at):
    return int(at.timestamp()) * 1000


def candles(kind="LONG_ENTRY"):
    closes = ["100"] * 50 + [{"LONG_ENTRY": "101", "EXIT": "99", "NO_ACTION": "100"}[kind]]
    return [
        [
            millis(NOW - timedelta(minutes=5 * (51 - i))),
            c,
            c,
            c,
            c,
            "1",
            millis(NOW - timedelta(minutes=5 * (50 - i))) - 1,
        ]
        for i, c in enumerate(closes)
    ]


class OfflineSource:
    def __init__(self, kind="LONG_ENTRY", btc="0"):
        self.calls = []
        self.responses = {
            "time": {"serverTime": millis(NOW)},
            "exchangeInfo": {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "isSpotTradingAllowed": True,
                        "status": "TRADING",
                        "orderTypes": ["MARKET"],
                        "filters": [
                            {
                                "filterType": "LOT_SIZE",
                                "minQty": "0.00001",
                                "maxQty": "100",
                                "stepSize": "0.00001",
                            }
                        ],
                    }
                ]
            },
            "klines": candles(kind),
            "account": {
                "balances": [
                    {"asset": "BTC", "free": btc, "locked": "0"},
                    {"asset": "USDT", "free": "100", "locked": "0"},
                ]
            },
            "openOrders": [],
            "trades": [{"price": "50000", "time": millis(NOW)}],
        }

    def read(self, resource, parameters=()):
        assert resource in self.responses
        self.calls.append((resource, parameters))
        return deepcopy(self.responses[resource])


def prepare(activation, tmp_path, source):
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    return prepare_check_only(
        source=source,
        now=lambda: NOW,
        release=activation["release"],
        wheel=activation["wheel"],
        tq=activation["tq"],
        tq_evidence=activation["tq_evidence"],
        credential_source_identity=activation["credential_source_identity"],
        credential_authority=activation["credential_authority"],
        workspace=tmp_path,
        ledger=TestnetSubmissionLedger.create(tmp_path / "check.sqlite"),
    )


@pytest.mark.parametrize(
    "kind,status",
    [("LONG_ENTRY", "READY_TO_SUBMIT"), ("NO_ACTION", "BLOCKED"), ("EXIT", "BLOCKED")],
)
def test_real_baseline_check_only(activation, tmp_path, monkeypatch, kind, status):
    import socket

    from atp.first_testnet_order.preparation_runtime import CheckOnlyTransport

    def forbidden(*args, **kwargs):
        raise AssertionError("Economic transport/network forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(CheckOnlyTransport, "submit", forbidden)
    source = OfflineSource(kind)
    result = prepare(activation, tmp_path, source)
    assert result["status"] == status, result
    assert result["strategy_signal"] == kind
    assert result["real_economic_calls"] == 0
    if status == "READY_TO_SUBMIT":
        assert result["ledger_state"] == "NOT_ATTEMPTED"
        assert result["ops"] == "READY"
        assert result["promotion"] == "ALLOWED"
        selection = result["quantity_selection"]
        assert Decimal(selection["selected_quantity"]) == Decimal("0.00010")
        assert Decimal(selection["projected_quote_notional"]) == 5


def test_exposure_uses_normal_risk_rejection(activation, tmp_path):
    result = prepare(activation, tmp_path, OfflineSource(btc="0.01"))
    assert result["status"] == "BLOCKED"
    assert result["risk_status"] == "REJECTED"


@pytest.mark.parametrize(
    "resource,value",
    [
        ("account", {}),
        ("openOrders", None),
        ("klines", []),
        ("time", {"serverTime": 0}),
        ("trades", [{"price": "50000", "time": millis(NOW - timedelta(seconds=11))}]),
    ],
)
def test_invalid_runtime_evidence_blocks(activation, tmp_path, resource, value):
    source = OfflineSource()
    source.responses[resource] = value
    try:
        result = prepare(activation, tmp_path, source)
    except EvidenceError:
        return
    assert result["status"] == "BLOCKED"
