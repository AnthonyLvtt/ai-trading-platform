import ast
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atp.accounting.model import AccountingValuation
from atp.shared.identity import ContentIdentity
from atp.web import WebState, create_app, project, reference
from atp.web.model import WebError
from atp.web.projections import ROUTES
from tests.contract.test_web_visibility import real_state, ref


@pytest.mark.parametrize("route", ROUTES)
def test_missing_artifact_is_404(route):
    client = TestClient(create_app(WebState()))
    response = client.get(route)
    assert response.status_code == 404
    assert response.json()["reason_code"] == "ARTIFACT_NOT_AVAILABLE"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("route", ROUTES)
def test_mutating_methods_are_not_allowed(method, route):
    response = TestClient(create_app(WebState())).request(method, route)
    assert response.status_code == 405


def test_closed_surface_no_docs_sessions_cors_or_reflected_headers(tmp_path):
    app = create_app(real_state(tmp_path))
    assert {r.path for r in app.routes} == set(ROUTES)
    assert all(r.methods == {"GET", "HEAD"} for r in app.routes)
    assert not app.user_middleware
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json", "/strategy/run", "/live/enable", "/ops/start"):
        assert client.get(path).status_code == 404
    response = client.get(
        "/health",
        headers={
            "Authorization": "Bearer fixture-private",
            "Cookie": "fixture-private",
            "X-API-Key": "fixture-private",
        },
    )
    assert response.status_code == 200
    assert "fixture-private" not in response.text
    assert "set-cookie" not in response.headers
    assert not any(k.startswith("access-control") for k in response.headers)
    head = client.head("/health")
    assert head.status_code == 200 and head.content == b""


@pytest.mark.parametrize("route", ROUTES)
def test_identity_is_of_actual_returned_json_and_is_deterministic(tmp_path, route):
    state = real_state(tmp_path)
    client = TestClient(create_app(state))
    response = client.get(route)
    assert response.content == client.get(route).content
    data = response.json()
    identity = data.pop("view_identity")
    assert identity == str(ContentIdentity.from_canonical(data))
    assert not {"authorized", "live_allowed", "trading_allowed"} & data.keys()


@pytest.mark.parametrize(
    "field,route",
    [
        ("health_result", "/health"),
        ("readiness_result", "/readiness"),
        ("qualification_result", "/qualification"),
        ("audit_journal", "/observability"),
    ],
)
def test_pinned_objects_detect_well_typed_tampering(tmp_path, field, route):
    state = real_state(tmp_path)
    item = getattr(state, field).artifact
    if field == "health_result":
        object.__setattr__(item.evidence, "config_loaded", False)
    elif field == "readiness_result":
        object.__setattr__(item, "config_identity", ContentIdentity.from_text("changed"))
    elif field == "qualification_result":
        object.__setattr__(item, "suite_version", "1.1")
    else:
        object.__setattr__(item, "events", ())
    response = TestClient(create_app(state)).get(route)
    assert response.status_code == 409
    assert response.json()["reason_code"] == "ARTIFACT_INTEGRITY_FAILURE"


@pytest.mark.parametrize("value", [None, 1, "bad", [], {}, object()])
def test_runtime_input_is_deterministically_blocked(value):
    client = TestClient(create_app(value))
    first = client.get("/health")
    assert first.status_code == 409
    assert first.json()["reason_code"] == "INVALID_WEB_ARTIFACT"
    assert first.content == client.get("/health").content
    assert isinstance(reference(value), WebError)


def test_untrusted_repr_is_never_invoked():
    class Hostile:
        def __str__(self):
            raise AssertionError("str called")

        def __repr__(self):
            raise AssertionError("repr called")

    assert isinstance(reference(Hostile()), WebError)
    assert TestClient(create_app(Hostile())).get("/health").status_code == 409


def test_sensitive_artifact_blocks_without_leakage(tmp_path):
    state = real_state(tmp_path)
    object.__setattr__(state.qualification_result.artifact, "suite_id", "api_key=fixture-sensitive")
    response = TestClient(create_app(state)).get("/qualification")
    assert response.status_code == 409
    assert response.json()["reason_code"] == "SENSITIVE_DATA_DETECTED"
    assert "fixture-sensitive" not in response.text
    result = reference({"nested": {"ToKeN": "fixture-sensitive"}})
    assert result.reason_code == "SENSITIVE_DATA_DETECTED"
    assert "fixture-sensitive" not in result.model_dump_json()


def test_linked_state_inconsistency_blocks(tmp_path):
    state = real_state(tmp_path)
    from atp.ops.model import StartupCheck

    other = ContentIdentity.from_text("other-qualified-result")
    source = state.readiness_result.artifact
    altered = replace(
        source,
        qualification_result_identity=other,
        startup_checks=tuple(
            replace(check, evidence_identity=other)
            if check.check is StartupCheck.QUALIFICATION_VALID
            else check
            for check in source.startup_checks
        ),
    )
    state = replace(state, readiness_result=ref(altered))
    response = TestClient(create_app(state)).get("/readiness")
    assert response.status_code == 409
    assert response.json()["reason_code"] == "WEB_STATE_INCONSISTENT"


def test_invalid_candidate_never_skipped(tmp_path):
    state = real_state(tmp_path)
    bad = replace(state.backtest_results[0].artifact)
    reference_before_tamper = ref(bad, state.backtest_results[0].causal_input)
    object.__setattr__(bad, "number_of_fills", 999)
    state = replace(state, backtest_results=(*state.backtest_results, reference_before_tamper))
    assert TestClient(create_app(state)).get("/backtest/latest").status_code == 409


def test_backtest_tie_break_and_collection_order(tmp_path):
    state = real_state(tmp_path)
    first = state.backtest_results[0]
    from atp.backtesting import BacktestInput, SimulatedPositionState
    from tests.unit.test_backtesting_engine import empty_portfolio, replay, replay_step, snapshot

    data = snapshot()
    step = replay_step(data, 3, empty_portfolio())
    result = replay(data, step)
    second = ref(result, BacktestInput(data, (step,), SimulatedPositionState.empty()))
    candidates = (first, second)
    client = TestClient(create_app(WebState(backtest_results=candidates)))
    response = client.get("/backtest/latest")
    assert response.status_code == 200
    assert response.json()["result_identity"] == max(str(r.content_identity) for r in candidates)
    assert (
        response.content
        == TestClient(create_app(WebState(backtest_results=tuple(reversed(candidates)))))
        .get("/backtest/latest")
        .content
    )


def test_accounting_prefers_valuation_and_decimal_strings(tmp_path):
    state = real_state(tmp_path)
    client = TestClient(create_app(state))
    value = client.get("/accounting/latest").json()
    assert value["source"] == "VALUATION"
    assert all(
        isinstance(value[k], str)
        for k in ("cash", "equity", "unrealized_pnl", "cumulative_realized_pnl")
    )
    replay = project(replace(state, accounting_valuations=()), "/accounting/latest")
    assert replay.source == "REPLAY"
    assert replay.position_state == "OPEN_LONG"


def test_later_valuation_and_changed_identity(tmp_path):
    state = real_state(tmp_path)
    first = state.accounting_valuations[0].artifact
    later = AccountingValuation.create(
        **{
            k: getattr(first, k)
            for k in first.__dataclass_fields__
            if k not in ("content_identity", "accounting_valuation_id", "valuation_time")
        },
        valuation_time=first.valuation_time + timedelta(minutes=1),
    )
    result = project(
        replace(state, accounting_valuations=(ref(later), *state.accounting_valuations)),
        "/accounting/latest",
    )
    assert result.accounting_identity == str(later.content_identity)
    assert result.view_identity != project(state, "/accounting/latest").view_identity


def test_tampered_accounting_before_and_after_reference(tmp_path):
    state = real_state(tmp_path)
    valuation = state.accounting_valuations[0].artifact
    object.__setattr__(valuation, "cash", Decimal("999"))
    assert reference(valuation).reason_code == "ARTIFACT_INTEGRITY_FAILURE"
    assert TestClient(create_app(state)).get("/accounting/latest").status_code == 409


def test_web_has_no_business_or_network_authority():
    prohibited = (
        "atp.oms",
        "atp.exchange",
        "atp.ai",
        "atp.ml",
        "atp.strategy.engine",
        "atp.risk.engine",
        "socket",
        "httpx",
        "requests",
        "urllib",
        "subprocess",
    )
    for path in Path("src/atp/web").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith(prohibited) for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(prohibited)
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert name not in (
                    "replay",
                    "evaluate",
                    "evaluate_suite",
                    "readiness",
                    "health",
                    "shutdown",
                    "now",
                    "utcnow",
                    "connect",
                    "open",
                    "read_text",
                    "glob",
                )


def test_bad_nested_runtime_types_and_cyclic_inputs(tmp_path):
    state = real_state(tmp_path)
    object.__setattr__(state.backtest_results[0].artifact.steps[0].fill, "fill_price", "1")
    assert TestClient(create_app(state)).get("/backtest/latest").status_code == 409
    cyclic = []
    cyclic.append(cyclic)
    assert isinstance(reference(cyclic), WebError)


def test_route_execution_requires_no_network(tmp_path, monkeypatch):
    import socket

    state = real_state(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("network operation")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    client = TestClient(create_app(state))
    for route in ROUTES:
        assert client.get(route).status_code == 200


def test_runtime_malformed_reference_is_safe(tmp_path):
    state = real_state(tmp_path)
    object.__setattr__(state.readiness_result, "artifact", object())
    assert TestClient(create_app(state)).get("/readiness").status_code == 409


def test_collection_with_invalid_valuation_never_falls_back_to_replay(tmp_path):
    state = real_state(tmp_path)
    object.__setattr__(state.accounting_valuations[0].artifact, "equity", Decimal("20.125"))
    response = TestClient(create_app(state)).get("/accounting/latest")
    assert response.status_code == 409
    assert "cash" not in response.json()


@pytest.mark.parametrize("replacement_identity", [False, True])
def test_readiness_tampered_before_reference_is_not_blessed(tmp_path, replacement_identity):
    from atp.shared.environment import Environment

    result = real_state(tmp_path).readiness_result.artifact
    original = result.content_identity
    object.__setattr__(result, "environment", Environment.TEST)
    object.__setattr__(
        result,
        "content_identity",
        ContentIdentity.from_text("forged-readiness") if replacement_identity else original,
    )
    rejected = reference(result)
    assert isinstance(rejected, WebError)
    assert rejected.reason_code == "ARTIFACT_INTEGRITY_FAILURE"
    # Inspection must not repair/re-pin a corrupt artefact.
    assert result.content_identity == (
        ContentIdentity.from_text("forged-readiness") if replacement_identity else original
    )


def test_readiness_rehashed_but_inconsistent_config_is_rejected(tmp_path):
    from atp.shared.environment import Environment

    result = real_state(tmp_path).readiness_result.artifact
    object.__setattr__(result, "environment", Environment.TEST)
    object.__setattr__(result, "content_identity", result.recompute_content_identity())
    assert reference(result).reason_code == "ARTIFACT_INTEGRITY_FAILURE"


@pytest.mark.parametrize(
    "name",
    [
        "readiness_result",
        "qualification_result",
        "audit_journal",
        "backtest_results",
        "accounting_replay_results",
        "accounting_valuations",
    ],
)
def test_every_stored_domain_identity_is_checked_before_pin(tmp_path, name):
    state = real_state(tmp_path)
    original = getattr(state, name)
    original = original[0] if isinstance(original, tuple) else original
    object.__setattr__(
        original.artifact,
        "content_identity",
        ContentIdentity.from_text("arbitrary-domain-identity"),
    )
    result = reference(original.artifact, causal_input=original.causal_input)
    assert isinstance(result, WebError)
    assert result.reason_code == "ARTIFACT_INTEGRITY_FAILURE"


def test_qualification_nested_tampering_before_pin_is_rejected(tmp_path):
    result = real_state(tmp_path).qualification_result.artifact
    original = result.content_identity
    case = result.case_results[0]
    names = list(case.evaluated_evidence_ids)
    names[0] = "altered-evidence"
    object.__setattr__(case, "evaluated_evidence_ids", tuple(sorted(names)))
    assert reference(result).reason_code == "ARTIFACT_INTEGRITY_FAILURE"
    assert result.content_identity == original


def test_health_status_must_match_evidence_before_pin(tmp_path):
    from atp.ops.model import HealthStatus

    result = real_state(tmp_path).health_result.artifact
    object.__setattr__(result, "health_status", HealthStatus.UNHEALTHY)
    assert reference(result).reason_code == "ARTIFACT_INTEGRITY_FAILURE"


def test_web_imports_only_public_cross_module_symbols():
    for path in Path("src/atp/web").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("atp.")
                and not node.module.startswith("atp.web")
            ):
                assert all(not alias.name.startswith("_") for alias in node.names)


def test_public_inspectors_are_read_only_and_fail_closed(tmp_path):
    from atp.accounting.inspection import validate_accounting_replay, validate_accounting_valuation
    from atp.backtesting.inspection import backtest_causal_time
    from atp.ops.inspection import validate_readiness_result
    from atp.test_qualification.inspection import validate_qualification_result

    state = real_state(tmp_path)
    for check, result in (
        (validate_readiness_result, state.readiness_result.artifact),
        (validate_qualification_result, state.qualification_result.artifact),
        (validate_accounting_replay, state.accounting_replay_results[0].artifact),
        (validate_accounting_valuation, state.accounting_valuations[0].artifact),
    ):
        assert check(result)
        assert not check(None)
        assert not check(object())
        before = result.content_identity
        assert check(result) and result.content_identity == before
    bt = state.backtest_results[0]
    assert backtest_causal_time(bt.artifact, bt.causal_input) == bt.artifact.steps[0].fill.fill_time
    assert backtest_causal_time(object(), None) is None
