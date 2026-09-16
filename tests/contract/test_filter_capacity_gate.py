"""Capacity checked at the final gate; refresh never changes authorization pins."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from atp.exchange.filters import parse_open_orders, parse_symbol_filters
from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.execution import run_first_order
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.preparation_runtime import prepare_check_only
from tests.activation_support import activation
from tests.contract.test_check_only_preparation import OfflineSource, millis
from tests.first_order_support import first_order
from tests.unit.test_filter_applicability import payload
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs", "first_order"]


@pytest.mark.parametrize(
    "age,reason",
    [(0, "READY_TO_SUBMIT"), (10, "READY_TO_SUBMIT"), (11, "OPEN_ORDERS_EVIDENCE_STALE")],
)
def test_final_gate_capacity_age(first_order, age, reason):
    values, deps = first_order
    raw = json.loads(values.filters.payload)
    raw["filters"].append({"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200})
    values = replace(
        values,
        filters=parse_symbol_filters(raw, NOW),
        open_orders=parse_open_orders([], "BTCUSDT", NOW - timedelta(seconds=age), complete=True),
    )
    identity = values.authorization.content_identity
    result = run_first_order(values, **deps)
    assert result.reason_code.value == reason
    assert values.authorization.content_identity == identity
    assert deps["transport"].calls == 0
    assert result.state.value == "NOT_ATTEMPTED"


@pytest.mark.parametrize("mode", ["fresh", "refresh", "failed", "changed", "partial"])
def test_after_second_pin_refresh_is_read_only(activation, tmp_path, monkeypatch, mode):
    import socket

    from atp.first_testnet_order.preparation_runtime import CheckOnlyTransport

    def forbidden(*args, **kwargs):
        raise AssertionError("no economic call or real network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(CheckOnlyTransport, "submit", forbidden)
    clock = [NOW]

    class Source(OfflineSource):
        reads = 0

        def read(self, resource, parameters=()):
            if resource == "time":
                return {"serverTime": millis(clock[0])}
            if resource == "openOrders":
                self.reads += 1
                if self.reads == 2:
                    if mode == "failed":
                        raise EvidenceError("READ_ONLY_SOURCE_UNAVAILABLE")
                    if mode == "partial":
                        return {"orders": [], "partial": True}
                    if mode == "changed":
                        return [{"symbol": "BTCUSDT", "orderId": 1, "status": "NEW"}]
            return super().read(resource, parameters)

    source = Source()
    source.responses["exchangeInfo"] = {"symbols": [payload()]}
    source.responses["avgPrice"] = {"mins": 5, "price": "50000", "closeTime": millis(NOW)}
    artifacts = {}
    pins = {}

    def save(name, artifact):
        artifacts[name] = artifact

    def pin(kind):
        # Synthetic independent approval boundary in tests only.
        pins[kind] = artifacts[kind].content_identity
        if kind == "first-order-authorization" and mode != "fresh":
            clock[0] += timedelta(seconds=11)
        return pins[kind]

    (tmp_path / "artifacts").mkdir()

    def run():
        return prepare_check_only(
            source=source,
            now=lambda: clock[0],
            release=activation["release"],
            wheel=activation["wheel"],
            tq=activation["tq"],
            tq_evidence=activation["tq_evidence"],
            credential_source_identity=activation["credential_source_identity"],
            credential_authority=activation["credential_authority"],
            workspace=tmp_path,
            ledger=TestnetSubmissionLedger.create(tmp_path / "ledger.sqlite"),
            artifact_sink=save,
            trust_pin_source=pin,
        )

    if mode in ("failed", "changed", "partial"):
        with pytest.raises(EvidenceError):
            run()
    else:
        result = run()
        assert result["reason_code"] == (
            "READY_TO_SUBMIT" if mode == "fresh" else "PRICE_EVIDENCE_STALE"
        )
        assert result["real_economic_calls"] == 0
        assert result["ledger_state"] == "NOT_ATTEMPTED"
    assert source.reads == (1 if mode == "fresh" else 2)
    assert artifacts["activation-grant"].content_identity == pins["activation-grant"]
    assert (
        artifacts["first-order-authorization"].content_identity == pins["first-order-authorization"]
    )
