from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from atp.accounting import (
    AccountingEngine,
    AccountingExecution,
    AccountingMark,
    AccountingReplayInput,
)
from atp.observability import AuditJournal, ObservationContext, observe_simulated_fill
from atp.ops import health, observability_evidence, readiness
from atp.ops.model import OperationalHealthEvidence, identity
from atp.shared.environment import Environment
from atp.shared.identity import CorrelationId
from atp.web import ArtifactReference, HealthResult, WebState, create_app, reference
from tests.contract.test_observability_vertical_slice import _completed_backtest
from tests.unit.test_ops import config, qualification


def ref(artifact, causal_input=None):
    result = reference(artifact, causal_input=causal_input)
    assert isinstance(result, ArtifactReference), result
    return result


def real_state(tmp_path, environment="BACKTEST"):
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    proof = qualification()
    ready = readiness(
        config(tmp_path, environment), qualification=proof, observability=observability_evidence()
    )
    data = dict(
        config_loaded=True,
        internal_components_available=True,
        observability_functional=True,
        fatal_error_present=False,
    )
    evidence = OperationalHealthEvidence(**data, content_identity=identity(data))
    snapshot, replay_input, replay = _completed_backtest(
        snapshot_created_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    fill = replay.steps[0].fill
    assert fill is not None
    accounting = AccountingEngine().replay(
        AccountingReplayInput(Decimal("100"), "USDT", (AccountingExecution(fill, Decimal("2")),))
    )
    mark = AccountingMark.from_data(point=snapshot.points[-1], snapshot=snapshot)
    valuation = AccountingEngine().value(
        replay_result=accounting, mark=mark, valuation_time=fill.fill_time
    )
    observed = observe_simulated_fill(
        fill,
        environment=Environment.BACKTEST,
        context=ObservationContext(CorrelationId("web-contract")),
    )
    journal = AuditJournal.empty().append(observed.event).journal
    assert journal is not None
    return WebState(
        ref(HealthResult(health(evidence), evidence)),
        ref(ready),
        ref(proof.result),
        ref(journal),
        (ref(replay, replay_input),),
        (ref(accounting),),
        (ref(valuation),),
    )


def test_real_six_route_contract(tmp_path):
    state = real_state(tmp_path)
    client = TestClient(create_app(state))
    for route in (
        "/health",
        "/readiness",
        "/qualification",
        "/observability",
        "/backtest/latest",
        "/accounting/latest",
    ):
        response = client.get(route)
        assert response.status_code == 200, (route, response.text)
        assert response.json()["status"] == "OK"
    assert client.get("/qualification").json()["qualification_status"] == "PASSED"
    assert client.get("/readiness").json()["readiness_status"] == "READY"
    accounting = client.get("/accounting/latest").json()
    assert accounting["source"] == "VALUATION"
    assert isinstance(accounting["equity"], str)
    backtest = client.get("/backtest/latest").json()
    assert backtest["latest_causal_time"].startswith("2026-01-01")


def test_live_remains_blocked_while_health_is_healthy(tmp_path):
    client = TestClient(create_app(real_state(tmp_path, "LIVE")))
    assert client.get("/health").json()["health_status"] == "HEALTHY"
    result = client.get("/readiness")
    assert result.status_code == 200
    assert result.json()["readiness_status"] == "BLOCKED"
    assert result.json()["reason_code"] == "LIVE_FORBIDDEN"
