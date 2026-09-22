"""ENG-TO-OPS-006: exact, read-only, non-authorizing pre-watch quantity feasibility."""

import ast
import json
import subprocess
import sys
from argparse import Namespace
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from atp.exchange.filters import (
    NotionalPriceEvidence,
    check_market_filters,
    check_market_quantity,
    parse_open_orders,
    parse_symbol_filters,
)
from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order import feasibility, preparation_http
from atp.first_testnet_order.feasibility import (
    FeasibilityStatus,
    assess_quantity_feasibility,
    check_pre_watch_feasibility,
)
from atp.first_testnet_order.model import FIRST_ORDER_QUOTE_CAP
from atp.first_testnet_order.preparation import select_quantity
from atp.shared.identity import ContentIdentity
from tests.unit.test_check_only_http import cli_module

NOW = datetime(2026, 9, 15, tzinfo=UTC)
PRICES = ("40000", "50000", "62500", "83333.33333333", "100000", "120000", "160000", "250000")
MILLIS = int(NOW.timestamp() * 1000)
FILTER_AGE = timedelta(minutes=15)


def symbol_payload():
    return json.loads(Path("tests/fixtures/exchange/btcusdt-exchange-info.json").read_text())[
        "symbols"
    ][0]


def filters(payload=None, observed_at=NOW):
    result = parse_symbol_filters(symbol_payload() if payload is None else payload, observed_at)
    assert result is not None
    return result


def price(value, *, effective_at=NOW, observed_at=NOW, minutes=5, kind="EXCHANGE_AVERAGE_PRICE"):
    return NotionalPriceEvidence(
        "BTCUSDT",
        Decimal(value),
        kind,
        ContentIdentity.from_text("offline price " + value),
        observed_at,
        effective_at,
        minutes,
    )


def assess(avg, projected=None, **kwargs):
    return assess_quantity_feasibility(
        kwargs.pop("evidence", filters()),
        price(avg),
        price(avg if projected is None else projected),
        kwargs.pop("at", NOW),
    )


# --- CTO-mandated outcomes -------------------------------------------------------------


def test_average_below_projected_without_grid_solution_is_not_feasible():
    result = assess("130000", "160000")
    assert result.status is FeasibilityStatus.NOT_FEASIBLE
    assert result.reason_code == "NO_ADMISSIBLE_QUANTITY"
    assert result.witness_quantity is None
    assert result.grid_index_min > result.grid_index_max


@pytest.mark.parametrize(("avg", "projected"), [("160000", "160000"), ("160000", "159000")])
def test_average_not_below_projected_but_no_exact_grid_solution_is_not_feasible(avg, projected):
    result = assess(avg, projected)
    assert result.status is FeasibilityStatus.NOT_FEASIBLE
    assert result.reason_code == "NO_ADMISSIBLE_QUANTITY"
    assert result.witness_quantity is None


@pytest.mark.parametrize(
    ("avg", "projected", "quantity"),
    [
        ("50000", "50000", "0.00012"),
        ("100000", "100000", "0.00006"),
        ("62500", "62500", "0.00009"),
        # Exactly on the cap: 0.0001 * 60000 == 6.
        ("60000", "60000", "0.0001"),
        # A wider window exists only when the projection is below the notional price.
        ("50000", "49000", "0.00012"),
    ],
)
def test_exact_valid_quantity_is_feasible(avg, projected, quantity):
    result = assess(avg, projected)
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.reason_code is None
    assert result.witness_quantity == Decimal(quantity)
    assert result.witness_quantity * Decimal(avg) >= 5
    assert result.witness_quantity * Decimal(projected) <= 6


def test_no_hidden_tolerance_or_implicit_rounding_around_the_cap_boundary():
    # 0.0001 * 60000 == 6 exactly; one hundred-millionth above, one more grid step is refused.
    assert assess("60000").witness_quantity == Decimal("0.0001")
    assert assess("59999.99999999").witness_quantity == Decimal("0.0001")
    above = assess("60000.00000001")
    assert above.witness_quantity == Decimal("0.00009")
    assert (above.witness_quantity + above.grid_step) * Decimal("60000.00000001") > 6
    # Inside an empty window nothing is rounded into an order.
    for value in ("160000", "160000.00000001", "166666", "150000.00000001"):
        assert assess(value).status is FeasibilityStatus.NOT_FEASIBLE


def test_cap_remains_exactly_six_and_is_never_a_float():
    assert type(FIRST_ORDER_QUOTE_CAP) is Decimal
    assert Decimal("6") == FIRST_ORDER_QUOTE_CAP
    result = assess("50000")
    assert result.quote_cap == FIRST_ORDER_QUOTE_CAP
    # The pre-check and the real selection can never diverge on the cap.
    orders = parse_open_orders([], "BTCUSDT", NOW, complete=True)
    selection = select_quantity(filters(), price("50000"), NOW, orders)
    assert selection.quote_cap == result.quote_cap
    assert selection.selected_quantity == result.witness_quantity


def test_module_never_uses_float_and_has_no_grant_pin_or_strategy_dependency():
    tree = ast.parse(Path(feasibility.__file__).read_text())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "float" not in names
    for forbidden in (
        "TestnetActivationGrant",
        "trust_pin_source",
        "external_pin",
        "SmaCrossoverStrategy",
        "StrategyEvaluationContext",
        "FirstTestnetOrderAuthorization",
        "TestnetSubmissionLedger",
        "TestnetReadOnlySource",
    ):
        assert forbidden not in names


def test_feasibility_never_authorizes_or_performs_a_side_effect():
    result = assess("50000")
    assert result.submission_authorized is False and result.side_effect_performed is False
    with pytest.raises(EvidenceError, match="SIDE_EFFECT_NOT_AUTHORIZED"):
        replace(result, submission_authorized=True)


# --- exactness against the shared MARKET filter logic ----------------------------------


@pytest.mark.parametrize(
    ("avg", "projected", "limit"),
    [(a, p, 60) for a in PRICES for p in PRICES] + [("1000", "1000", 700), ("2000", "1500", 700)],
)
def test_closed_form_agrees_with_brute_force_over_the_shared_filters(avg, projected, limit):
    evidence = filters()
    average = price(avg)
    orders = parse_open_orders([], "BTCUSDT", NOW, complete=True)
    step = Decimal("0.00001")
    admissible = [
        k
        for k in range(1, limit)
        if check_market_quantity(evidence, "BTCUSDT", k * step, NOW, average) is None
        and k * step * Decimal(projected) <= 6
    ]
    result = assess(avg, projected)
    if admissible:
        assert result.status is FeasibilityStatus.FEASIBLE
        assert result.witness_quantity == max(admissible) * step
        # The full gate, including live capacity, agrees when real capacity evidence exists.
        assert (
            check_market_filters(evidence, "BTCUSDT", result.witness_quantity, NOW, average, orders)
            is None
        )
    else:
        assert result.status is FeasibilityStatus.NOT_FEASIBLE


def test_disabled_market_step_uses_generic_grid_and_no_finite_grid_is_never_invented():
    assert assess("50000").grid_step == Decimal("0.00001")
    payload = symbol_payload()
    for item in payload["filters"]:
        if item["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE"):
            item["stepSize"] = "0.00000000"
    with pytest.raises(EvidenceError):
        assess("50000", evidence=filters(payload))
    payload = symbol_payload()
    payload["filters"] = [f for f in payload["filters"] if f["filterType"] != "MARKET_LOT_SIZE"]
    for item in payload["filters"]:
        if item["filterType"] == "LOT_SIZE":
            item["stepSize"] = "0.00000000"
    result = assess("50000", evidence=filters(payload))
    assert result.status is FeasibilityStatus.NOT_FEASIBLE and result.grid_step == 0


def test_maximum_quantity_bound_applies():
    payload = symbol_payload()
    for item in payload["filters"]:
        if item["filterType"] == "MARKET_LOT_SIZE":
            item["maxQty"] = "0.00005"
    assert assess("50000", evidence=filters(payload)).status is FeasibilityStatus.NOT_FEASIBLE


def test_maximum_notional_bound_applies_when_it_is_the_binding_upper_bound():
    payload = symbol_payload()
    for item in payload["filters"]:
        if item["filterType"] == "NOTIONAL":
            item["applyMaxToMarket"] = True
            item["maxNotional"] = "5.50000000"
    result = assess("100000", "10000", evidence=filters(payload))
    assert result.status is FeasibilityStatus.FEASIBLE
    # The cap alone would allow 0.0006; the applicable maximum notional binds first.
    assert result.witness_quantity == Decimal("0.00005")
    assert result.witness_quantity * Decimal("100000") <= Decimal("5.5")


def test_notional_minimum_not_applied_to_market_removes_that_lower_bound():
    payload = symbol_payload()
    for item in payload["filters"]:
        if item["filterType"] == "NOTIONAL":
            item["applyMinToMarket"] = False
    last = price("60000", kind="EXCHANGE_LAST_PRICE", minutes=0)
    result = assess_quantity_feasibility(filters(payload), last, last, NOW)
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.witness_quantity == Decimal("0.0001")


# --- BLOCKED (fail-closed) evidence --------------------------------------------------


def test_unknown_filter_is_blocked_never_ignored():
    payload = symbol_payload()
    payload["filters"].append({"filterType": "FUTURE_FILTER"})
    assert parse_symbol_filters(payload, NOW) is None
    with pytest.raises(EvidenceError):
        assess_quantity_feasibility(None, price("50000"), price("50000"), NOW)


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda p: p.update(status="BREAK"), "INVALID_FILTER_EVIDENCE"),
        (lambda p: p.update(orderTypes=["LIMIT"]), "INVALID_FILTER_EVIDENCE"),
    ],
)
def test_symbol_not_tradable_for_market_is_blocked_not_infeasible(mutate, reason):
    payload = deepcopy(symbol_payload())
    mutate(payload)
    with pytest.raises(EvidenceError, match=reason):
        assess("50000", evidence=filters(payload))


def test_projected_price_must_satisfy_the_same_exchange_price_contract():
    average = price("50000")
    last = price("50000", kind="EXCHANGE_LAST_PRICE", minutes=0)
    # The regression: a valid average price paired with a last-price projection is BLOCKED.
    with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
        assess_quantity_feasibility(filters(), average, last, NOW)
    for incompatible in (
        price("50000", minutes=3),
        price("50000", kind="EXCHANGE_LAST_PRICE", minutes=5),
        price("50000", kind="EXCHANGE_AVERAGE_PRICE", minutes=0),
    ):
        with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
            assess_quantity_feasibility(filters(), average, incompatible, NOW)
    # Reversed roles and both-incompatible inputs are blocked too, never a verdict.
    with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
        assess_quantity_feasibility(filters(), last, average, NOW)
    with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
        assess_quantity_feasibility(filters(), last, last, NOW)


def test_projected_price_symbol_and_freshness_are_enforced_independently():
    average = price("50000")
    other = NotionalPriceEvidence(
        "ETHUSDT",
        Decimal("50000"),
        "EXCHANGE_AVERAGE_PRICE",
        ContentIdentity.from_text("other symbol"),
        NOW,
        NOW,
        5,
    )
    with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
        assess_quantity_feasibility(filters(), average, other, NOW)
    stale = price("50000", effective_at=NOW - timedelta(seconds=11))
    with pytest.raises(EvidenceError, match="PRICE_EVIDENCE_STALE"):
        assess_quantity_feasibility(filters(), average, stale, NOW)
    future = price("50000", observed_at=NOW + timedelta(seconds=1))
    with pytest.raises(EvidenceError, match="TIME_EVIDENCE_INVALID"):
        assess_quantity_feasibility(filters(), average, future, NOW)
    with pytest.raises(EvidenceError):
        assess_quantity_feasibility(filters(), average, "not evidence", NOW)


def test_average_projection_is_rejected_when_no_notional_filter_applies_to_market():
    payload = symbol_payload()
    for item in payload["filters"]:
        if item["filterType"] == "NOTIONAL":
            item["applyMinToMarket"] = False
    last = price("60000", kind="EXCHANGE_LAST_PRICE", minutes=0)
    with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
        assess_quantity_feasibility(filters(payload), last, price("60000"), NOW)


def at_time(avg, when, **kwargs):
    fresh = price(avg, effective_at=when, observed_at=when)
    return assess_quantity_feasibility(filters(**kwargs), fresh, fresh, when)


def test_freshness_boundaries_are_inclusive_and_fail_closed():
    boundary = NOW + FILTER_AGE
    assert at_time("50000", boundary).status is FeasibilityStatus.FEASIBLE
    with pytest.raises(EvidenceError, match="SYMBOL_FILTER_EVIDENCE_STALE"):
        at_time("50000", boundary + timedelta(microseconds=1))
    assert assess("50000", at=NOW + timedelta(seconds=10)).status is FeasibilityStatus.FEASIBLE
    with pytest.raises(EvidenceError, match="PRICE_EVIDENCE_STALE"):
        assess("50000", at=NOW + timedelta(seconds=10, microseconds=1))


def test_invalid_time_and_incompatible_price_source_are_blocked():
    with pytest.raises(EvidenceError, match="TIME_EVIDENCE_INVALID"):
        assess_quantity_feasibility(
            filters(), price("50000", observed_at=NOW + timedelta(seconds=1)), price("50000"), NOW
        )
    with pytest.raises(EvidenceError, match="FIRST_ORDER_NOT_READY"):
        assess_quantity_feasibility(
            filters(), price("50000", kind="EXCHANGE_LAST_PRICE", minutes=0), price("50000"), NOW
        )
    with pytest.raises(EvidenceError, match="INVALID_FILTER_EVIDENCE"):
        assess("50000", evidence=filters(observed_at=NOW + timedelta(seconds=1)))
    with pytest.raises(EvidenceError, match="TIME_EVIDENCE_INVALID"):
        assess("50000", at=NOW.replace(tzinfo=None))


# --- runtime composition: public, credential-free, no economic path ---------------------


class FakeSource:
    def __init__(self, average="50000.00000000", *, server_time=MILLIS, fail=None, live=False):
        self.average, self.server_time, self.fail, self.live, self.calls = (
            average,
            server_time,
            fail,
            live,
            [],
        )

    def _now(self):
        return int(datetime.now(UTC).timestamp() * 1000) if self.live else self.server_time

    def read(self, resource, parameters=()):
        self.calls.append((resource, parameters))
        if self.fail == resource:
            raise EvidenceError("READ_ONLY_SOURCE_UNAVAILABLE")
        if resource == "time":
            return {"serverTime": self._now()}
        if resource == "exchangeInfo":
            return {"symbols": [symbol_payload()]}
        if resource == "avgPrice":
            return {"mins": 5, "price": self.average, "closeTime": self._now()}
        raise AssertionError("resource outside the public feasibility surface: " + resource)


def run_check(source):
    return check_pre_watch_feasibility(source=source, now=lambda: NOW)


def test_runtime_reads_only_public_routes_and_reports_no_side_effects():
    source = FakeSource()
    report = run_check(source)
    assert report["status"] == "FEASIBLE" and report["reason_code"] is None
    assert {call[0] for call in source.calls} <= {"time", "exchangeInfo", "avgPrice"}
    assert report["feasibility"]["witness_quantity"] == "0.00012"
    for key, expected in {
        "strategy_evaluated": False,
        "grant_created": False,
        "trust_pin_requested": False,
        "submission_authorized": False,
        "real_economic_calls": 0,
        "LIVE": "LIVE_FORBIDDEN",
        "quote_cap": "6",
    }.items():
        assert report[key] == expected
    assert "strategy_signal" not in report and "activation_grant_identity" not in report


def test_runtime_not_feasible_is_reported_and_never_relaxed():
    report = run_check(FakeSource("160000"))
    assert report["status"] == "NOT_FEASIBLE"
    assert report["reason_code"] == "NO_ADMISSIBLE_QUANTITY"
    assert report["quote_cap"] == "6"
    assert report["real_economic_calls"] == 0 and report["LIVE"] == "LIVE_FORBIDDEN"


@pytest.mark.parametrize("fail", ["time", "exchangeInfo", "avgPrice"])
def test_runtime_source_failure_is_blocked_never_feasible(fail):
    report = run_check(FakeSource(fail=fail))
    assert report["status"] == "BLOCKED"
    assert report["reason_code"] == "READ_ONLY_SOURCE_UNAVAILABLE"


def test_runtime_rejects_skewed_time_stale_price_and_malformed_payloads():
    assert run_check(FakeSource(server_time=MILLIS + 6000))["status"] == "BLOCKED"

    class StalePrice(FakeSource):
        def read(self, resource, parameters=()):
            if resource == "avgPrice":
                self.calls.append((resource, parameters))
                return {"mins": 5, "price": "50000", "closeTime": MILLIS - 11000}
            return super().read(resource, parameters)

    report = run_check(StalePrice())
    assert (report["status"], report["reason_code"]) == ("BLOCKED", "PRICE_EVIDENCE_STALE")
    for average in ("0", "-1", "abc", 50000, None):
        assert run_check(FakeSource(average))["status"] == "BLOCKED"


def test_public_source_has_no_credential_or_economic_surface(monkeypatch):
    seen = []

    class Connection:
        def __init__(self, host, timeout):
            assert host == "testnet.binance.vision"

        def request(self, method, path, headers):
            assert method == "GET" and not headers
            seen.append(path)

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda limit: b'{"serverTime": 1}')

        def close(self):
            pass

    monkeypatch.setattr(preparation_http, "HTTPSConnection", Connection)
    source = preparation_http.PublicTestnetSource()
    assert source.read("time") == {"serverTime": 1}
    for resource in ("account", "openOrders", "klines", "order", "cancel", "withdraw", "../order"):
        with pytest.raises(EvidenceError):
            source.read(resource)
    assert seen == ["/api/v3/time"]
    assert not hasattr(source, "_provider")


# --- CLI: standalone mode and the watcher gate -----------------------------------------


def watch_args(tmp_path):
    return Namespace(
        check_only=True,
        prepare=False,
        confirm_testnet_permissions=True,
        session_dir=tmp_path / "session",
        source_commit="0" * 40,
        release_version="0.0.0",
        activation_grant_pin=None,
        first_order_authorization_pin=None,
        request_trust_pins=False,
        watch=True,
    )


def test_watcher_never_starts_when_not_feasible(tmp_path, monkeypatch):
    module = cli_module()
    args = watch_args(tmp_path)
    source = FakeSource("160000", live=True)
    report = module.run(args, window=object(), feasibility_source=source)
    assert report["status"] == "NOT_FEASIBLE" and report["reason_code"] == "NO_ADMISSIBLE_QUANTITY"
    assert not args.session_dir.exists()
    assert {call[0] for call in source.calls} <= {"time", "exchangeInfo", "avgPrice"}
    assert report["grant_created"] is False and report["trust_pin_requested"] is False


def test_watcher_never_starts_when_feasibility_is_blocked(tmp_path):
    module = cli_module()
    args = watch_args(tmp_path)
    report = module.run(
        args, window=object(), feasibility_source=FakeSource(fail="avgPrice", live=True)
    )
    assert report["status"] == "BLOCKED" and not args.session_dir.exists()


def test_feasible_watch_proceeds_to_the_existing_credential_gate(tmp_path, monkeypatch):
    module = cli_module()
    for name in ("ATP_BINANCE_TESTNET_API_KEY", "ATP_BINANCE_TESTNET_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    args = watch_args(tmp_path)
    with pytest.raises(EvidenceError, match="CREDENTIAL_CAPABILITY_INVALID"):
        module.run(args, window=object(), feasibility_source=FakeSource(live=True))
    assert not args.session_dir.exists()


def test_non_watch_check_only_is_not_gated(tmp_path, monkeypatch):
    module = cli_module()
    for name in ("ATP_BINANCE_TESTNET_API_KEY", "ATP_BINANCE_TESTNET_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    source = FakeSource("160000")
    with pytest.raises(EvidenceError, match="CREDENTIAL_CAPABILITY_INVALID"):
        module.run(watch_args(tmp_path), window=None, feasibility_source=source)
    assert source.calls == []


@pytest.mark.parametrize(
    ("average", "code", "status"),
    [("50000.00000000", 0, "FEASIBLE"), ("160000.00000000", 2, "NOT_FEASIBLE")],
)
def test_standalone_mode_prints_report_and_exit_code(monkeypatch, capsys, average, code, status):
    module = cli_module()
    monkeypatch.setattr(module, "PublicTestnetSource", lambda: FakeSource(average))
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: NOW))
    monkeypatch.setattr(sys, "argv", ["submit_first_testnet_order.py", "--pre-watch-feasibility"])
    assert module.main() == code
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == status
    assert report["real_economic_calls"] == 0 and report["transport_call_count"] == 0
    assert report["LIVE"] == "LIVE_FORBIDDEN"
    assert report["grant_created"] is False and report["trust_pin_requested"] is False


@pytest.mark.parametrize(
    "flags", [["--execute"], ["--prepare", "--watch"], ["--execute", "--watch"]]
)
def test_economic_and_preparation_modes_remain_unavailable(flags):
    result = subprocess.run(
        [sys.executable, "scripts/submit_first_testnet_order.py", *flags],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_pre_watch_mode_is_exclusive_with_other_modes():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/submit_first_testnet_order.py",
            "--pre-watch-feasibility",
            "--execute",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "not allowed with" in result.stderr
