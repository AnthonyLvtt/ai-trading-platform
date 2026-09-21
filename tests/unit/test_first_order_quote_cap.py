"""ENG-TO-OPS-007: one explicit 6 USDT first Testnet order quote cap, never widened."""

import ast
import pathlib
from dataclasses import fields, replace
from decimal import Decimal, InvalidOperation

import pytest

from atp.exchange.filters import NotionalPriceEvidence, parse_open_orders
from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order import preparation_runtime
from atp.first_testnet_order.feasibility import FeasibilityStatus, assess_quantity_feasibility
from atp.first_testnet_order.gate import _freshness
from atp.first_testnet_order.model import FIRST_ORDER_QUOTE_CAP, FirstOrderPolicy, Reason
from atp.first_testnet_order.preparation import QuantitySelectionEvidence, select_quantity
from atp.shared.identity import ContentIdentity
from tests.activation_support import activation
from tests.contract.test_check_only_preparation import OfflineSource, prepare
from tests.first_order_support import first_order
from tests.unit.test_pre_watch_feasibility import NOW, filters, price
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW as GATE_NOW

__all__ = ["activation", "first_order", "inputs"]

SIX = Decimal("6")
SRC = pathlib.Path("src")


def orders():
    return parse_open_orders([], "BTCUSDT", NOW, complete=True)


# --- one source of truth ---------------------------------------------------------------


def test_cap_is_exactly_six_and_a_decimal():
    assert type(FIRST_ORDER_QUOTE_CAP) is Decimal
    assert FIRST_ORDER_QUOTE_CAP == SIX
    assert str(FIRST_ORDER_QUOTE_CAP) == "6"


def test_policy_identity_is_unchanged_by_the_cap():
    policy = FirstOrderPolicy()
    assert (policy.policy_id, policy.policy_version) == ("ATP_FIRST_TESTNET_ORDER_V1", "1.0")
    # The cap is an authorization parameter, not a policy field.
    assert not any("quote" in field.name or "notional" in field.name for field in fields(policy))


def test_selection_and_feasibility_use_the_same_cap():
    selection = select_quantity(filters(), price("50000"), NOW, orders())
    assessed = assess_quantity_feasibility(filters(), price("50000"), price("50000"), NOW)
    assert selection.quote_cap == assessed.quote_cap == FIRST_ORDER_QUOTE_CAP
    assert selection.selected_quantity == assessed.witness_quantity == Decimal("0.00012")
    assert QuantitySelectionEvidence.__dataclass_fields__["quote_cap"].default is (
        FIRST_ORDER_QUOTE_CAP
    )


def test_runtime_built_authorization_carries_the_same_cap(activation, tmp_path, monkeypatch):
    seen = []
    real = preparation_runtime._freshness

    def recording(authorization, *args, **kwargs):
        seen.append(authorization)
        return real(authorization, *args, **kwargs)

    monkeypatch.setattr(preparation_runtime, "_freshness", recording)
    result = prepare(activation, tmp_path, OfflineSource())
    assert result["status"] == "READY_TO_SUBMIT", result
    assert seen
    assert {auth.max_quote_notional for auth in seen} == {FIRST_ORDER_QUOTE_CAP}
    assert Decimal(result["quantity_selection"]["projected_quote_notional"]) <= SIX
    # Selection, feasibility and authorization can no longer read 6 / 6 / 5.
    assert Decimal(result["quantity_selection"]["quote_cap"]) == seen[0].max_quote_notional


def test_previous_authorization_at_five_is_a_different_identity(first_order):
    values, _ = first_order
    at_six = replace(values.authorization, max_quote_notional=SIX)
    at_five = replace(values.authorization, max_quote_notional=Decimal("5"))
    assert at_five.content_identity != at_six.content_identity
    assert at_five.authorization_facts_identity != at_six.authorization_facts_identity
    assert at_five.client_order_id != at_six.client_order_id


# --- never above 6, never adjusted -----------------------------------------------------


def test_gate_accepts_exactly_six_and_refuses_anything_above(first_order):
    values, deps = first_order
    auth = replace(
        values.authorization, quantity=Decimal("0.00012"), max_quote_notional=FIRST_ORDER_QUOTE_CAP
    )

    def at(value):
        evidence = NotionalPriceEvidence(
            "BTCUSDT",
            Decimal(value),
            "EXCHANGE_LAST_PRICE",
            ContentIdentity.from_text("boundary-price"),
            GATE_NOW,
            GATE_NOW,
            0,
        )
        return _freshness(auth, values.filters, evidence, deps["clock"])[0]

    assert at("50000") is None  # 0.00012 * 50000 == 6 exactly
    assert at("49999.99999999") is None
    assert at("50000.00000001") is Reason.NOTIONAL_LIMIT_EXCEEDED
    assert at("60000") is Reason.NOTIONAL_LIMIT_EXCEEDED


PRICE_SAMPLE = [Decimal(value) for value in range(20000, 400001, 997)] + [
    Decimal(value) for value in ("59999.99999999", "60000", "60000.00000001", "100000", "160000")
]


@pytest.mark.parametrize("value", PRICE_SAMPLE)
def test_selection_is_within_five_and_six_or_refused_and_agrees_with_feasibility(value):
    evidence, average = filters(), price(str(value))
    assessed = assess_quantity_feasibility(evidence, average, average, NOW)
    try:
        selection = select_quantity(evidence, average, NOW, orders())
    except EvidenceError as error:
        # No admissible quantity: the cap is not raised and nothing is fabricated.
        assert error.args == ("NO_ADMISSIBLE_QUANTITY",)
        assert assessed.status is FeasibilityStatus.NOT_FEASIBLE
        return
    assert assessed.status is FeasibilityStatus.FEASIBLE
    assert assessed.witness_quantity == selection.selected_quantity
    notional = selection.selected_quantity * value
    assert Decimal("5") <= notional <= SIX  # minNotional respected, never above the cap
    assert selection.projected_quote_notional == notional
    assert selection.quote_cap == SIX
    # Maximal admissible: one more grid step would break the cap.
    assert (selection.selected_quantity + selection.step_size) * value > SIX


def test_no_admissible_quantity_never_raises_the_cap_or_relaxes_the_minimum():
    for value in ("160000", "200001", "249999", "400000"):
        average = price(value)
        with pytest.raises(EvidenceError, match="NO_ADMISSIBLE_QUANTITY"):
            select_quantity(filters(), average, NOW, orders())
        assert (
            assess_quantity_feasibility(filters(), average, average, NOW).status
            is FeasibilityStatus.NOT_FEASIBLE
        )


def test_feasible_window_at_six_is_not_empty_up_to_120000():
    for value in range(30000, 120001, 1009):
        average = price(str(value))
        assert (
            assess_quantity_feasibility(filters(), average, average, NOW).status
            is FeasibilityStatus.FEASIBLE
        )


# --- regression guard: no economic 5 or duplicated 6 in the active path -------------------

CAP_NAMES = ("quote", "notional", "cap")


def number(node):
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        try:
            return Decimal(str(node.value))
        except (InvalidOperation, ValueError):
            return None
    return None


def call_name(node):
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def economic_literals(source, values=(5,)):
    """Line numbers of Decimal/Fraction(n), comparisons against n and cap-named n bindings."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and call_name(node) in ("Decimal", "Fraction"):
            if node.args and number(node.args[0]) in values:
                lines.append(node.lineno)
            for keyword in node.keywords:
                if number(keyword.value) in values and any(
                    c in (keyword.arg or "") for c in CAP_NAMES
                ):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if number(keyword.value) in values and any(
                    c in (keyword.arg or "") for c in CAP_NAMES
                ):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Compare):
            if any(number(side) in values for side in (node.left, *node.comparators)):
                lines.append(node.lineno)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            capped = any(c in name.lower() for name in names for c in CAP_NAMES)
            if node.value is not None and number(node.value) in values and capped:
                lines.append(node.lineno)
    return sorted(set(lines))


@pytest.mark.parametrize(
    "source",
    [
        'Decimal("5")',
        "Decimal(5)",
        'Decimal("5.00000000")',
        "Fraction(5) / price",
        "projected > 5",
        "5 <= projected",
        "quote_cap = 5",
        "cap: int = 5",
        "make(max_quote_notional=5)",
        'Decimal(max_quote_notional="5")',
    ],
)
def test_guard_detects_a_reintroduced_five(source):
    assert economic_literals(source) == [1]


@pytest.mark.parametrize(
    "source",
    [
        "timedelta(seconds=5)",
        "timedelta(minutes=5)",
        "abs(delta) > timedelta(seconds=5)",
        "avg_price_minutes = 5",
        "Decimal(50)",
        'Decimal("0.5")',
        "count == 51",
        "FIRST_ORDER_QUOTE_CAP",
    ],
)
def test_guard_ignores_unrelated_five_second_and_five_minute_constants(source):
    assert economic_literals(source) == []


def test_guard_detects_a_duplicated_hard_coded_six():
    assert economic_literals('cap = Decimal("6")', (6,)) == [1]
    assert economic_literals("projected > 6", (6,)) == [1]


def test_no_economic_five_reappears_anywhere_in_source_or_scripts():
    offenders = {}
    for path in [*SRC.rglob("*.py"), *pathlib.Path("scripts").glob("*.py")]:
        lines = economic_literals(path.read_text())
        if lines:
            offenders[str(path)] = lines
    assert offenders == {}, "old 5 USDT cap literal reintroduced"


def test_the_six_is_defined_once_and_only_in_the_model():
    model = SRC / "atp" / "first_testnet_order" / "model.py"
    for path in SRC.rglob("*.py"):
        lines = economic_literals(path.read_text(), (6,))
        if path == model:
            assert len(lines) == 1
        else:
            assert lines == [], str(path)


def test_cap_is_read_from_the_constant_in_every_consumer():
    package = SRC / "atp" / "first_testnet_order"
    for name in ("preparation.py", "preparation_runtime.py", "feasibility.py"):
        assert "FIRST_ORDER_QUOTE_CAP" in (package / name).read_text(), name
