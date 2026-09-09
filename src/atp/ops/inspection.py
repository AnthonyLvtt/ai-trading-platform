"""Public read-only verification of already produced OPS results."""

from dataclasses import fields

from atp.ops.engine import _PRIORITY, _record, _valid_identity, health
from atp.ops.model import (
    CheckStatus,
    HealthStatus,
    OperationalConfig,
    OperationalEnvironment,
    OperationalHealthEvidence,
    OperationalPolicy,
    OperationalReadinessResult,
    ReadinessStatus,
    StartupCheck,
    StartupCheckResult,
    identity,
)
from atp.ops.model import (
    OperationalReasonCode as Reason,
)
from atp.shared.errors import ValidationError


def validate_health_result(status: object, evidence: object) -> bool:
    """Inspect the evidence/status pair; never produce a readiness decision."""
    if type(status) is not HealthStatus or type(evidence) is not OperationalHealthEvidence:
        return False
    assert isinstance(evidence, OperationalHealthEvidence)
    try:
        data = _record(evidence)
        return (
            all(v is None or type(v) is bool for v in data.values())
            and _valid_identity(evidence.content_identity)
            and evidence.content_identity == identity(data)
            and status is health(evidence)
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        return False


def validate_readiness_result(value: object) -> bool:
    """Verify stored identity, config/check links and aggregation, without startup IO."""
    if type(value) is not OperationalReadinessResult:
        return False
    assert isinstance(value, OperationalReadinessResult)
    try:
        if (
            type(value.environment) not in (OperationalEnvironment, type(None))
            or type(value.readiness_status) is not ReadinessStatus
            or type(value.reason_code) is not Reason
            or type(value.startup_checks) is not tuple
        ):
            return False
        if not all(
            _valid_identity(v)
            for v in (value.content_identity, value.config_identity, value.ops_policy_identity)
        ):
            return False
        if value.ops_policy_identity != OperationalPolicy().content_identity:
            return False
        for ref in (value.qualification_result_identity, value.observability_evidence_identity):
            if ref is not None and not _valid_identity(ref):
                return False
        for check in value.startup_checks:
            if (
                type(check) is not StartupCheckResult
                or type(check.check) is not StartupCheck
                or type(check.status) is not CheckStatus
                or type(check.reason_code) is not Reason
                or check.critical is not True
                or (
                    check.evidence_identity is not None
                    and not _valid_identity(check.evidence_identity)
                )
            ):
                return False
            if (check.status is CheckStatus.PASSED) != (
                check.reason_code in (Reason.OPERATIONAL_READY, Reason.NOT_REQUIRED)
            ):
                return False
        checks = {c.check: c for c in value.startup_checks}
        if len(checks) != len(value.startup_checks) or set(checks) != set(StartupCheck):
            return False
        blocked = {c.reason_code for c in value.startup_checks if c.status is CheckStatus.BLOCKED}
        if (value.readiness_status is ReadinessStatus.BLOCKED) != bool(blocked):
            return False
        if value.reason_code is not next(
            (r for r in _PRIORITY if r in blocked), Reason.OPERATIONAL_READY
        ):
            return False
        inactive = {
            OperationalEnvironment.LIVE: Reason.LIVE_FORBIDDEN,
            OperationalEnvironment.TESTNET: Reason.TESTNET_NOT_AUTHORIZED,
            OperationalEnvironment.DRY_RUN: Reason.ENVIRONMENT_INACTIVE,
        }
        if value.environment in inactive and value.reason_code is not inactive[value.environment]:
            return False
        if (
            checks[StartupCheck.QUALIFICATION_VALID].evidence_identity
            != value.qualification_result_identity
        ):
            return False
        if (
            checks[StartupCheck.OBSERVABILITY_AVAILABLE].evidence_identity
            != value.observability_evidence_identity
        ):
            return False
        if value.readiness_status is ReadinessStatus.READY:
            if value.environment not in (
                OperationalEnvironment.LOCAL,
                OperationalEnvironment.TEST,
                OperationalEnvironment.BACKTEST,
                OperationalEnvironment.SIMULATION,
            ):
                return False
            if value.config is None or value.observability_evidence_identity is None:
                return False
            if (
                value.environment
                in (OperationalEnvironment.BACKTEST, OperationalEnvironment.SIMULATION)
                and value.qualification_result_identity is None
            ):
                return False
        if value.config is not None:
            config = value.config
            if type(config) is not OperationalConfig or config.environment is not value.environment:
                return False
            # Canonical config fields contain only enums, strings, booleans and an identity.
            for f in fields(config):
                v = getattr(config, f.name)
                expected_type = (
                    OperationalEnvironment
                    if f.name == "environment"
                    else bool
                    if f.name
                    in ("observability_enabled", "qualification_required", "deterministic_mode")
                    else str
                )
                if f.name != "content_identity" and type(v) is not expected_type:
                    return False
            if (
                not _valid_identity(config.content_identity)
                or config.content_identity != value.config_identity
            ):
                return False
            if identity(_record(config)) != value.config_identity:
                return False
        return value.content_identity == value.recompute_content_identity()
    except (AttributeError, TypeError, ValueError, KeyError, ValidationError):
        return False
