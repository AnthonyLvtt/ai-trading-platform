"""Operational checks grant neither trading nor release authority."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import cast

from atp.observability.audit import AuditJournal, validate_journal
from atp.observability.events import EVENT_SCHEMA_VERSION, SENSITIVE_KEYS, validate_event
from atp.observability.logging import DiagnosticLogRecord
from atp.ops.filesystem import FilesystemBoundary, LocalFilesystem
from atp.ops.model import (
    CheckStatus,
    HealthStatus,
    ObservabilityReadinessEvidence,
    OperationalConfig,
    OperationalEnvironment,
    OperationalHealthEvidence,
    OperationalPolicy,
    OperationalReadinessResult,
    OperationalState,
    QualificationReference,
    ReadinessStatus,
    ShutdownReason,
    ShutdownRequest,
    ShutdownResult,
    ShutdownStatus,
    StartupCheck,
    StartupCheckResult,
    identity,
)
from atp.ops.model import (
    OperationalReasonCode as Reason,
)
from atp.shared.identity import ContentIdentity
from atp.test_qualification.inspection import validate_qualification_result
from atp.test_qualification.model import (
    QualificationStatus,
)

_ERRORS = (ValueError, TypeError, AttributeError, KeyError, RecursionError)
_PRIORITY = (
    Reason.LIVE_FORBIDDEN,
    Reason.TESTNET_NOT_AUTHORIZED,
    Reason.ENVIRONMENT_INACTIVE,
    Reason.UNKNOWN_ENVIRONMENT,
    Reason.CREDENTIAL_MATERIAL_FORBIDDEN,
    Reason.INVALID_OPERATIONAL_CONFIG,
    Reason.REQUIRED_CONFIG_MISSING,
    Reason.QUALIFICATION_REQUIRED,
    Reason.QUALIFICATION_INVALID,
    Reason.OBSERVABILITY_UNAVAILABLE,
    Reason.OBSERVABILITY_INVALID,
    Reason.STARTUP_CHECK_FAILED,
)


def _valid_identity(value: object) -> bool:
    if type(value) is not ContentIdentity:
        return False
    assert isinstance(value, ContentIdentity)
    return (
        type(value.algorithm) is str
        and value.algorithm == "sha256"
        and type(value.digest) is str
        and len(value.digest) == 64
        and all(c in "0123456789abcdef" for c in value.digest)
    )


def _fallback(value: object) -> ContentIdentity:
    cls = type(value)
    module = type.__getattribute__(cls, "__module__")
    name = type.__getattribute__(cls, "__qualname__")
    return identity(
        {
            "module": module if type(module) is str else "unknown",
            "type": name if type(name) is str else "unknown",
        }
    )


def _record(value: object) -> dict[str, object]:
    return {f.name: getattr(value, f.name) for f in fields(value) if f.name != "content_identity"}  # type: ignore[arg-type]


def _credentials(value: object) -> bool:
    if type(value) is dict:
        return any(
            type(k) is str
            and (
                k.casefold() in SENSITIVE_KEYS
                or k.casefold() in ("exchange_credentials", "withdrawal_address")
            )
            or _credentials(v)
            for k, v in value.items()
        )
    if type(value) in (tuple, list):
        return any(_credentials(v) for v in cast(list[object], value))
    if type(value) is str:
        return any(
            s in value.casefold()
            for s in ("bearer ", "-----begin", "ghp_", "github_pat_", "sk-proj-")
        )
    return False


def observability_evidence() -> ObservabilityReadinessEvidence:
    """Inspect local contracts only; no event is emitted and no backend is contacted."""
    values = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "audit_journal_schema_version": AuditJournal.empty().schema_version,
        "event_identity_validation_available": callable(validate_event),
        "audit_chain_validation_available": callable(validate_journal),
        "diagnostic_logging_available": callable(DiagnosticLogRecord),
        "sensitive_data_guard_available": bool(SENSITIVE_KEYS),
    }
    return ObservabilityReadinessEvidence(**values, content_identity=identity(values))  # type: ignore[arg-type]


def _observability(value: object) -> bool:
    if type(value) is not ObservabilityReadinessEvidence:
        return False
    assert isinstance(value, ObservabilityReadinessEvidence)
    try:
        data = _record(value)
        return (
            type(data["event_schema_version"]) is str
            and data["event_schema_version"] == EVENT_SCHEMA_VERSION
            and type(data["audit_journal_schema_version"]) is str
            and data["audit_journal_schema_version"] == EVENT_SCHEMA_VERSION
            and all(data[k] is True for k in data if k.endswith("available"))
            and _valid_identity(value.content_identity)
            and value.content_identity == identity(data)
        )
    except _ERRORS:
        return False


def _qualification(value: object) -> bool:
    """Check the pinned reference against the public qualification inspection contract."""
    if type(value) is not QualificationReference:
        return False
    assert isinstance(value, QualificationReference)
    return (
        validate_qualification_result(value.result)
        and value.result.status is QualificationStatus.PASSED
        and _valid_identity(value.content_identity)
        and value.result.content_identity == value.content_identity
        and type(value.qualification_run_id) is str
        and value.result.qualification_run_id == value.qualification_run_id
    )


def health(value: object) -> HealthStatus:
    if type(value) is not OperationalHealthEvidence:
        return HealthStatus.UNKNOWN
    assert isinstance(value, OperationalHealthEvidence)
    try:
        data = _record(value)
        if not all(v is None or type(v) is bool for v in data.values()):
            return HealthStatus.UNKNOWN
        if not _valid_identity(value.content_identity) or value.content_identity != identity(data):
            return HealthStatus.UNKNOWN
        if value.fatal_error_present is True:
            return HealthStatus.UNHEALTHY
        if any(v is None for v in data.values()):
            return HealthStatus.UNKNOWN
        return (
            HealthStatus.HEALTHY
            if (
                value.config_loaded
                and value.internal_components_available
                and value.observability_functional
            )
            else HealthStatus.UNHEALTHY
        )
    except _ERRORS:
        return HealthStatus.UNKNOWN


def readiness(
    config: object,
    *,
    qualification: object = None,
    observability: object = None,
    filesystem: FilesystemBoundary | None = None,
    policy: object = OperationalPolicy(),
) -> OperationalReadinessResult:
    filesystem = filesystem if filesystem is not None else LocalFilesystem()
    reasons: dict[StartupCheck, Reason | None] = {c: None for c in StartupCheck}
    references: dict[StartupCheck, ContentIdentity] = {}
    env = None
    canonical = None
    config_id = _fallback(config)
    policy_id = OperationalPolicy().content_identity
    raw: object = config
    # Parse only external configuration at this boundary. Internal evaluation errors propagate.
    try:
        raw = _record(config) if type(config) is OperationalConfig else config
        if type(raw) is not dict:
            raise ValueError("configuration must be a record")
        env_value = raw.get("environment")
        if type(env_value) is OperationalEnvironment:
            env = env_value
        elif type(env_value) is str:
            try:
                env = OperationalEnvironment(env_value)
            except ValueError:
                env = None
        contaminated = _credentials(raw)
        if contaminated:
            reasons[StartupCheck.NO_FORBIDDEN_CREDENTIAL_MATERIAL] = (
                Reason.CREDENTIAL_MATERIAL_FORBIDDEN
            )
        required = {f.name for f in fields(OperationalConfig)} - {"content_identity"}
        if not required <= raw.keys():
            reasons[StartupCheck.CONFIG_VALID] = Reason.REQUIRED_CONFIG_MISSING
        elif (
            set(raw) != required
            or (
                any(
                    type(raw[k]) is not str
                    for k in (
                        "app_name",
                        "config_schema_version",
                        "workspace_path",
                        "artifacts_path",
                        "ops_policy_id",
                        "ops_policy_version",
                    )
                )
                or any(
                    type(raw[k]) is not bool
                    for k in (
                        "observability_enabled",
                        "qualification_required",
                        "deterministic_mode",
                    )
                )
            )
            or (
                raw["app_name"] != "ATP"
                or raw["config_schema_version"] != "1.0"
                or raw["observability_enabled"] is not True
                or raw["deterministic_mode"] is not True
                or raw["ops_policy_id"] != "ATP_OPS_V1"
                or raw["ops_policy_version"] != "1.0"
                or raw["qualification_required"]
                != (env in (OperationalEnvironment.BACKTEST, OperationalEnvironment.SIMULATION))
            )
        ):
            reasons[StartupCheck.CONFIG_VALID] = Reason.INVALID_OPERATIONAL_CONFIG
        if raw.get("deterministic_mode") is not True:
            reasons[StartupCheck.DETERMINISTIC_MODE_ENABLED] = Reason.INVALID_OPERATIONAL_CONFIG
        if reasons[StartupCheck.CONFIG_VALID] is None and not contaminated:
            if type(config) is OperationalConfig and (
                not _valid_identity(config.content_identity)
                or config.content_identity != identity(raw)
            ):
                raise ValueError("altered configuration")
            safe = dict(raw)
            safe["environment"] = env
            paths_valid = True
            for name, check in (
                ("workspace_path", StartupCheck.WORKSPACE_VALID),
                ("artifacts_path", StartupCheck.ARTIFACTS_PATH_VALID),
            ):
                path = raw[name]
                if (
                    not path
                    or path.strip() != path
                    or "://" in path
                    or path.startswith(("//", "\\\\"))
                ):
                    reasons[check] = Reason.INVALID_OPERATIONAL_CONFIG
                    paths_valid = False
            if paths_valid:
                try:
                    workspace = filesystem.resolve(raw["workspace_path"])
                    artifact_input = raw["artifacts_path"]
                    if not Path(artifact_input).is_absolute():
                        artifact_input = str(Path(workspace) / artifact_input)
                    artifacts = filesystem.resolve(artifact_input)
                    if type(workspace) is not str or type(artifacts) is not str:
                        raise ValueError("invalid resolved path")
                    if any(
                        not Path(p).is_absolute() or ".." in Path(p).parts
                        for p in (workspace, artifacts)
                    ):
                        raise ValueError("filesystem must return canonical local paths")
                    safe["workspace_path"], safe["artifacts_path"] = workspace, artifacts
                    if filesystem.is_directory(workspace) is not True:
                        reasons[StartupCheck.WORKSPACE_VALID] = Reason.INVALID_OPERATIONAL_CONFIG
                    if (
                        not Path(artifacts).is_relative_to(Path(workspace))
                        or filesystem.artifacts_available(artifacts) is not True
                    ):
                        reasons[StartupCheck.ARTIFACTS_PATH_VALID] = (
                            Reason.INVALID_OPERATIONAL_CONFIG
                        )
                except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
                    reasons[StartupCheck.WORKSPACE_VALID] = Reason.INVALID_OPERATIONAL_CONFIG
                    reasons[StartupCheck.ARTIFACTS_PATH_VALID] = Reason.INVALID_OPERATIONAL_CONFIG
            config_id = identity(safe)
            if env is not None:
                canonical = OperationalConfig(**safe, content_identity=config_id)
        else:
            reasons[StartupCheck.WORKSPACE_VALID] = Reason.STARTUP_CHECK_FAILED
            reasons[StartupCheck.ARTIFACTS_PATH_VALID] = Reason.STARTUP_CHECK_FAILED
    except _ERRORS:
        reasons[StartupCheck.DETERMINISTIC_MODE_ENABLED] = Reason.STARTUP_CHECK_FAILED
        if reasons[StartupCheck.NO_FORBIDDEN_CREDENTIAL_MATERIAL] is None:
            reasons[StartupCheck.NO_FORBIDDEN_CREDENTIAL_MATERIAL] = Reason.STARTUP_CHECK_FAILED
        reasons[StartupCheck.CONFIG_VALID] = Reason.INVALID_OPERATIONAL_CONFIG
        reasons[StartupCheck.WORKSPACE_VALID] = Reason.STARTUP_CHECK_FAILED
        reasons[StartupCheck.ARTIFACTS_PATH_VALID] = Reason.STARTUP_CHECK_FAILED
    try:
        if type(policy) is not OperationalPolicy:
            raise ValueError("invalid OPS policy")
        policy.__post_init__()
    except _ERRORS:
        reasons[StartupCheck.CONFIG_VALID] = Reason.INVALID_OPERATIONAL_CONFIG
    if env is None:
        unknown = Reason.UNKNOWN_ENVIRONMENT if type(raw) is dict else Reason.STARTUP_CHECK_FAILED
        reasons[StartupCheck.ENVIRONMENT_KNOWN] = unknown
        reasons[StartupCheck.ENVIRONMENT_ACTIVE] = unknown
    else:
        reasons[StartupCheck.ENVIRONMENT_ACTIVE] = {
            OperationalEnvironment.LIVE: Reason.LIVE_FORBIDDEN,
            OperationalEnvironment.TESTNET: Reason.TESTNET_NOT_AUTHORIZED,
            OperationalEnvironment.DRY_RUN: Reason.ENVIRONMENT_INACTIVE,
        }.get(env)
    qualification_id = None
    if env in (OperationalEnvironment.BACKTEST, OperationalEnvironment.SIMULATION):
        if qualification is None:
            reasons[StartupCheck.QUALIFICATION_VALID] = Reason.QUALIFICATION_REQUIRED
        elif not _qualification(qualification):
            reasons[StartupCheck.QUALIFICATION_VALID] = Reason.QUALIFICATION_INVALID
        else:
            assert isinstance(qualification, QualificationReference)
            qualification_id = qualification.content_identity
            references[StartupCheck.QUALIFICATION_VALID] = qualification_id
    elif env in (OperationalEnvironment.LOCAL, OperationalEnvironment.TEST):
        reasons[StartupCheck.QUALIFICATION_VALID] = Reason.NOT_REQUIRED
    else:
        reasons[StartupCheck.QUALIFICATION_VALID] = Reason.STARTUP_CHECK_FAILED
    obs_id = None
    if observability is None:
        reasons[StartupCheck.OBSERVABILITY_AVAILABLE] = Reason.OBSERVABILITY_UNAVAILABLE
    elif not _observability(observability):
        reasons[StartupCheck.OBSERVABILITY_AVAILABLE] = Reason.OBSERVABILITY_INVALID
    else:
        assert isinstance(observability, ObservabilityReadinessEvidence)
        obs_id = observability.content_identity
        references[StartupCheck.OBSERVABILITY_AVAILABLE] = obs_id
    checks = tuple(
        StartupCheckResult(
            c,
            CheckStatus.PASSED if r in (None, Reason.NOT_REQUIRED) else CheckStatus.BLOCKED,
            r or Reason.OPERATIONAL_READY,
            True,
            references.get(c),
        )
        for c, r in sorted(reasons.items())
    )
    blocked = {c.reason_code for c in checks if c.status is CheckStatus.BLOCKED}
    reason = next((r for r in _PRIORITY if r in blocked), Reason.OPERATIONAL_READY)
    return OperationalReadinessResult(
        env,
        ReadinessStatus.BLOCKED if blocked else ReadinessStatus.READY,
        reason,
        config_id,
        policy_id,
        checks,
        qualification_id,
        obs_id,
        None if reasons[StartupCheck.NO_FORBIDDEN_CREDENTIAL_MATERIAL] else canonical,
    )


def shutdown(request: object) -> ShutdownResult:
    fallback = _fallback(request)
    previous = None
    reason = None
    causal = None
    valid = False
    if type(request) is ShutdownRequest:
        assert isinstance(request, ShutdownRequest)
        try:
            previous = (
                request.requested_from_state
                if type(request.requested_from_state) is OperationalState
                else None
            )
            reason = request.reason if type(request.reason) is ShutdownReason else None
            causal = request.causal_evidence_identity
            valid = (
                previous is not None
                and reason is not None
                and (causal is None or _valid_identity(causal))
                and _valid_identity(request.content_identity)
                and request.content_identity == identity(_record(request))
            )
            if valid:
                fallback = request.content_identity
        except _ERRORS:
            valid = False
    targets = {
        OperationalState.STARTING: OperationalState.STOPPING,
        OperationalState.READY: OperationalState.STOPPING,
        OperationalState.BLOCKED: OperationalState.STOPPING,
        OperationalState.STOPPING: OperationalState.STOPPED,
    }
    if not valid or previous not in targets:
        return ShutdownResult(
            ShutdownStatus.BLOCKED,
            Reason.INVALID_SHUTDOWN_REQUEST,
            previous,
            previous,
            reason,
            None,
            fallback,
        )
    target = targets[previous]
    return ShutdownResult(
        ShutdownStatus.COMPLETED,
        Reason.SHUTDOWN_COMPLETED
        if target is OperationalState.STOPPED
        else Reason.SHUTDOWN_REQUESTED,
        previous,
        target,
        reason,
        causal,
        fallback,
    )
