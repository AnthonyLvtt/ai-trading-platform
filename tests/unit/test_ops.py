from dataclasses import replace
from pathlib import Path

import pytest

from atp.ops import (
    CheckStatus,
    HealthStatus,
    OperationalHealthEvidence,
    OperationalPolicy,
    OperationalState,
    QualificationReference,
    ReadinessStatus,
    ShutdownReason,
    ShutdownRequest,
    ShutdownStatus,
    StartupCheck,
    health,
    observability_evidence,
    readiness,
    shutdown,
)
from atp.ops import (
    OperationalReasonCode as Reason,
)
from atp.ops.model import identity
from atp.shared.identity import ContentIdentity
from atp.test_qualification import CASES_V1, SUITE_V1, evaluate_suite
from tests.unit.test_qualification_engine import bundle


def config(workspace, environment="LOCAL"):
    return dict(
        environment=environment,
        app_name="ATP",
        config_schema_version="1.0",
        workspace_path=str(workspace),
        artifacts_path=str(workspace / "artifacts"),
        observability_enabled=True,
        deterministic_mode=True,
        qualification_required=environment in ("BACKTEST", "SIMULATION"),
        ops_policy_id="ATP_OPS_V1",
        ops_policy_version="1.0",
    )


def qualification():
    subjects, evidence = bundle()
    result = evaluate_suite(SUITE_V1, CASES_V1, evidence, subjects=subjects)
    return QualificationReference(result, result.content_identity, result.qualification_run_id)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "artifacts").mkdir()
    return tmp_path


@pytest.mark.parametrize("environment", ["LOCAL", "TEST", "BACKTEST", "SIMULATION"])
def test_authorized_modes_are_ready(workspace, environment):
    result = readiness(
        config(workspace, environment),
        qualification=qualification(),
        observability=observability_evidence(),
    )
    assert result.readiness_status is ReadinessStatus.READY
    assert len(result.startup_checks) == 9
    assert all(c.critical and c.status is CheckStatus.PASSED for c in result.startup_checks)
    conditional = next(
        c for c in result.startup_checks if c.check is StartupCheck.QUALIFICATION_VALID
    )
    assert (conditional.reason_code is Reason.NOT_REQUIRED) == (environment in ("LOCAL", "TEST"))


@pytest.mark.parametrize(
    "environment,reason",
    [
        ("LIVE", Reason.LIVE_FORBIDDEN),
        ("TESTNET", Reason.TESTNET_NOT_AUTHORIZED),
        ("DRY_RUN", Reason.ENVIRONMENT_INACTIVE),
        ("unknown", Reason.UNKNOWN_ENVIRONMENT),
    ],
)
def test_forbidden_modes_block_even_with_valid_proofs(workspace, environment, reason):
    result = readiness(
        config(workspace, environment),
        qualification=qualification(),
        observability=observability_evidence(),
    )
    assert result.readiness_status is ReadinessStatus.BLOCKED
    assert result.reason_code is reason


def test_required_qualification_and_tampering(workspace):
    assert (
        readiness(config(workspace, "BACKTEST"), observability=observability_evidence()).reason_code
        is Reason.QUALIFICATION_REQUIRED
    )
    proof = qualification()
    case = proof.result.case_results[0]
    altered = tuple(
        ContentIdentity.from_text("altered") for _ in case.evaluated_evidence_identities
    )
    object.__setattr__(case, "evaluated_evidence_identities", altered)
    result = readiness(
        config(workspace, "SIMULATION"), qualification=proof, observability=observability_evidence()
    )
    assert result.reason_code is Reason.QUALIFICATION_INVALID


def test_qualification_cannot_be_a_bare_passed_flag(workspace):
    proof = qualification()
    object.__setattr__(proof.result, "case_results", ())
    # Re-pinning the corrupted structure must not bypass completeness validation.
    proof = QualificationReference(
        proof.result, proof.result.content_identity, proof.result.qualification_run_id
    )
    result = readiness(
        config(workspace, "BACKTEST"), qualification=proof, observability=observability_evidence()
    )
    assert result.reason_code is Reason.QUALIFICATION_INVALID


@pytest.mark.parametrize("field", ["workspace_path", "app_name", "qualification_required"])
def test_missing_config_fields_block(workspace, field):
    value = config(workspace)
    del value[field]
    assert (
        readiness(value, observability=observability_evidence()).reason_code
        is Reason.REQUIRED_CONFIG_MISSING
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("app_name", 1),
        ("deterministic_mode", False),
        ("observability_enabled", 1),
        ("qualification_required", False),
        ("ops_policy_version", "2"),
    ],
)
def test_wrong_config_types_and_policy_do_not_allow_execution(workspace, field, value):
    settings = config(workspace, "BACKTEST") | {field: value}
    result = readiness(
        settings, qualification=qualification(), observability=observability_evidence()
    )
    assert result.readiness_status is ReadinessStatus.BLOCKED
    assert result.reason_code is Reason.INVALID_OPERATIONAL_CONFIG


def test_workspace_and_paths_fail_closed(workspace):
    settings = config(workspace)
    assert (
        readiness(
            settings | {"workspace_path": str(workspace / "absent")},
            observability=observability_evidence(),
        ).readiness_status
        is ReadinessStatus.BLOCKED
    )
    for path in (str(workspace.parent), "s3://bucket", "//remote/share"):
        assert (
            readiness(
                settings | {"artifacts_path": path}, observability=observability_evidence()
            ).reason_code
            is Reason.INVALID_OPERATIONAL_CONFIG
        )
    (workspace / "escape").symlink_to(workspace.parent, target_is_directory=True)
    assert (
        readiness(
            settings | {"artifacts_path": str(workspace / "escape")},
            observability=observability_evidence(),
        ).reason_code
        is Reason.INVALID_OPERATIONAL_CONFIG
    )


def test_injected_filesystem_can_prove_artifacts_creatable(workspace):
    from atp.ops import LocalFilesystem

    class Creatable(LocalFilesystem):
        def artifacts_available(self, path):
            return Path(path).parent == workspace

    settings = config(workspace) | {"artifacts_path": str(workspace / "new")}
    result = readiness(settings, filesystem=Creatable(), observability=observability_evidence())
    assert result.readiness_status is ReadinessStatus.READY
    assert not (workspace / "new").exists()


@pytest.mark.parametrize("key", ["API_KEY", "token", "exchange_credentials", "Withdrawal_Address"])
def test_credentials_block_without_echo(workspace, key):
    result = readiness(
        config(workspace) | {"nested": [{key: "fixture-sensitive-value"}]},
        observability=observability_evidence(),
    )
    assert result.reason_code is Reason.CREDENTIAL_MATERIAL_FORBIDDEN
    assert result.config is None
    assert "fixture-sensitive-value" not in repr(result)


def test_live_reason_has_priority_over_bad_proofs_and_credentials(workspace):
    result = readiness(config(workspace, "LIVE") | {"api_key": "fixture-value"})
    assert result.reason_code is Reason.LIVE_FORBIDDEN
    assert result.config is None


def test_missing_and_tampered_observability_block(workspace):
    assert readiness(config(workspace)).reason_code is Reason.OBSERVABILITY_UNAVAILABLE
    proof = observability_evidence()
    object.__setattr__(proof, "diagnostic_logging_available", False)
    assert (
        readiness(config(workspace), observability=proof).reason_code
        is Reason.OBSERVABILITY_INVALID
    )


def test_same_inputs_and_canonical_paths_have_same_identity(workspace):
    settings = config(workspace)
    first = readiness(settings, observability=observability_evidence())
    assert first.config is not None
    assert readiness(first.config, observability=observability_evidence()) == first
    assert (
        readiness(
            settings | {"artifacts_path": str(workspace / "artifacts" / ".." / "artifacts")},
            observability=observability_evidence(),
        ).content_identity
        == first.content_identity
    )
    second = readiness(
        settings | {"artifacts_path": str(workspace)}, observability=observability_evidence()
    )
    assert second.config_identity != first.config_identity
    assert second.content_identity != first.content_identity


def test_health_and_readiness_are_independent(workspace):
    values = dict(
        config_loaded=True,
        internal_components_available=True,
        observability_functional=True,
        fatal_error_present=False,
    )
    proof = OperationalHealthEvidence(**values, content_identity=identity(values))
    assert health(proof) is HealthStatus.HEALTHY
    assert (
        readiness(config(workspace, "LIVE"), observability=observability_evidence()).reason_code
        is Reason.LIVE_FORBIDDEN
    )
    values["fatal_error_present"] = True
    assert (
        health(OperationalHealthEvidence(**values, content_identity=identity(values)))
        is HealthStatus.UNHEALTHY
    )
    assert health(object()) is HealthStatus.UNKNOWN
    values["fatal_error_present"] = None
    assert (
        health(OperationalHealthEvidence(**values, content_identity=identity(values)))
        is HealthStatus.UNKNOWN
    )


class BadObject:
    def __str__(self):
        raise AssertionError("must not stringify external values")

    def __repr__(self):
        raise AssertionError("must not repr external values")


def test_malformed_objects_block_deterministically(workspace):
    bad = BadObject()
    first = readiness(bad)
    assert first.readiness_status is ReadinessStatus.BLOCKED
    assert first.reason_code is Reason.INVALID_OPERATIONAL_CONFIG
    assert first.content_identity == readiness(BadObject()).content_identity
    assert (
        readiness(config(workspace) | {"workspace_path": bad}).readiness_status
        is ReadinessStatus.BLOCKED
    )
    assert (
        readiness(
            config(workspace, "BACKTEST"), qualification=bad, observability=observability_evidence()
        ).reason_code
        is Reason.QUALIFICATION_INVALID
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("live_allowed", True),
        ("version", "2"),
        ("qualification_required", ()),
        ("active_environments", ("LIVE",)),
    ],
)
def test_mutated_policy_is_refused(workspace, field, value):
    with pytest.raises(ValueError):
        replace(OperationalPolicy(), **{field: value})
    policy = OperationalPolicy()
    object.__setattr__(policy, field, value)
    assert (
        readiness(
            config(workspace), policy=policy, observability=observability_evidence()
        ).readiness_status
        is ReadinessStatus.BLOCKED
    )


def request(state):
    values = dict(
        reason=ShutdownReason.USER_REQUEST,
        requested_from_state=state,
        causal_evidence_identity=None,
    )
    return ShutdownRequest(**values, content_identity=identity(values))


def test_shutdown_only_moves_toward_stopped():
    first = shutdown(request(OperationalState.READY))
    assert first.status is ShutdownStatus.COMPLETED
    assert first.resulting_state is OperationalState.STOPPING
    second = shutdown(request(first.resulting_state))
    assert second.resulting_state is OperationalState.STOPPED
    assert second.reason_code is Reason.SHUTDOWN_COMPLETED
    blocked = shutdown(request(second.resulting_state))
    assert blocked.status is ShutdownStatus.BLOCKED
    assert blocked.resulting_state is OperationalState.STOPPED
    assert shutdown(object()).status is ShutdownStatus.BLOCKED
    assert shutdown(request(OperationalState.BLOCKED)).resulting_state is OperationalState.STOPPING


def test_tampered_config_health_and_shutdown_are_rejected(workspace):
    result = readiness(config(workspace), observability=observability_evidence())
    assert result.config is not None
    object.__setattr__(result.config, "workspace_path", str(workspace.parent))
    assert (
        readiness(result.config, observability=observability_evidence()).reason_code
        is Reason.INVALID_OPERATIONAL_CONFIG
    )
    values = dict(
        config_loaded=True,
        internal_components_available=True,
        observability_functional=True,
        fatal_error_present=False,
    )
    healthy = OperationalHealthEvidence(**values, content_identity=identity(values))
    object.__setattr__(healthy, "fatal_error_present", True)
    assert health(healthy) is HealthStatus.UNKNOWN
    stopping = request(OperationalState.READY)
    object.__setattr__(stopping, "requested_from_state", OperationalState.STARTING)
    assert shutdown(stopping).status is ShutdownStatus.BLOCKED


def test_bad_identity_objects_are_never_stringified(workspace):
    obs = observability_evidence()
    object.__setattr__(obs, "content_identity", BadObject())
    assert (
        readiness(config(workspace), observability=obs).reason_code is Reason.OBSERVABILITY_INVALID
    )
    proof = qualification()
    object.__setattr__(proof.result.case_results[0], "case_identity", BadObject())
    assert (
        readiness(
            config(workspace, "BACKTEST"),
            qualification=proof,
            observability=observability_evidence(),
        ).reason_code
        is Reason.QUALIFICATION_INVALID
    )


def test_check_order_does_not_change_result_identity(workspace):
    result = readiness(config(workspace), observability=observability_evidence())
    assert (
        replace(result, startup_checks=result.startup_checks[::-1]).content_identity
        == result.content_identity
    )
