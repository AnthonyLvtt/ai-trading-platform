"""OPS records contain no economic authority or credential fields."""

from __future__ import annotations

from dataclasses import dataclass, fields
from dataclasses import field as dataclass_field
from enum import StrEnum

from atp.shared.environment import Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.test_qualification.model import QualificationSuiteResult

OperationalEnvironment = Environment


class ReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class CheckStatus(StrEnum):
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


class StartupCheck(StrEnum):
    ENVIRONMENT_KNOWN = "ENVIRONMENT_KNOWN"
    ENVIRONMENT_ACTIVE = "ENVIRONMENT_ACTIVE"
    CONFIG_VALID = "CONFIG_VALID"
    WORKSPACE_VALID = "WORKSPACE_VALID"
    ARTIFACTS_PATH_VALID = "ARTIFACTS_PATH_VALID"
    NO_FORBIDDEN_CREDENTIAL_MATERIAL = "NO_FORBIDDEN_CREDENTIAL_MATERIAL"
    QUALIFICATION_VALID = "QUALIFICATION_VALID"
    OBSERVABILITY_AVAILABLE = "OBSERVABILITY_AVAILABLE"
    DETERMINISTIC_MODE_ENABLED = "DETERMINISTIC_MODE_ENABLED"


class OperationalReasonCode(StrEnum):
    OPERATIONAL_READY = "OPERATIONAL_READY"
    UNKNOWN_ENVIRONMENT = "UNKNOWN_ENVIRONMENT"
    ENVIRONMENT_INACTIVE = "ENVIRONMENT_INACTIVE"
    INVALID_OPERATIONAL_CONFIG = "INVALID_OPERATIONAL_CONFIG"
    REQUIRED_CONFIG_MISSING = "REQUIRED_CONFIG_MISSING"
    QUALIFICATION_REQUIRED = "QUALIFICATION_REQUIRED"
    QUALIFICATION_INVALID = "QUALIFICATION_INVALID"
    OBSERVABILITY_UNAVAILABLE = "OBSERVABILITY_UNAVAILABLE"
    OBSERVABILITY_INVALID = "OBSERVABILITY_INVALID"
    CREDENTIAL_MATERIAL_FORBIDDEN = "CREDENTIAL_MATERIAL_FORBIDDEN"
    LIVE_FORBIDDEN = "LIVE_FORBIDDEN"
    TESTNET_NOT_AUTHORIZED = "TESTNET_NOT_AUTHORIZED"
    STARTUP_CHECK_FAILED = "STARTUP_CHECK_FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"
    SHUTDOWN_REQUESTED = "SHUTDOWN_REQUESTED"
    SHUTDOWN_COMPLETED = "SHUTDOWN_COMPLETED"
    INVALID_SHUTDOWN_REQUEST = "INVALID_SHUTDOWN_REQUEST"


class OperationalState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    BLOCKED = "BLOCKED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class ShutdownReason(StrEnum):
    USER_REQUEST = "USER_REQUEST"
    STARTUP_BLOCKED = "STARTUP_BLOCKED"
    HEALTH_FAILURE = "HEALTH_FAILURE"
    QUALIFICATION_INVALIDATED = "QUALIFICATION_INVALIDATED"
    OBSERVABILITY_FAILURE = "OBSERVABILITY_FAILURE"
    INTERNAL_FATAL_ERROR = "INTERNAL_FATAL_ERROR"


class ShutdownStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


def identity(values: dict[str, object]) -> ContentIdentity:
    """Only primitive values or already validated identities may enter this helper."""
    return ContentIdentity.from_canonical(
        {
            key: str(value) if type(value) is ContentIdentity else value
            for key, value in values.items()
        }
    )


@dataclass(frozen=True, slots=True)
class OperationalPolicy:
    policy_id: str = "ATP_OPS_V1"
    version: str = "1.0"
    active_environments: tuple[str, ...] = ("LOCAL", "TEST", "BACKTEST", "SIMULATION")
    inactive_environments: tuple[str, ...] = ("DRY_RUN", "TESTNET", "LIVE")
    qualification_required: tuple[str, ...] = ("BACKTEST", "SIMULATION")
    observability_required: bool = True
    network_required: bool = False
    exchange_required: bool = False
    live_allowed: bool = False
    testnet_allowed: bool = False
    dry_run_allowed: bool = False
    external_orchestrator_required: bool = False
    secrets_allowed_in_operational_config: bool = False
    fail_closed: bool = True

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is tuple and any(type(v) is not str for v in value):
                raise ValueError("OPS policy tuple must contain strings")
            if type(value) is not type(field.default) or value != field.default:
                raise ValueError("OPS policy must be exact V1")

    @property
    def content_identity(self) -> ContentIdentity:
        return identity({f.name: getattr(self, f.name) for f in fields(self)})


@dataclass(frozen=True, slots=True)
class OperationalConfig:
    """Canonical configuration, returned only after filesystem resolution by OPS."""

    environment: OperationalEnvironment
    app_name: str
    config_schema_version: str
    workspace_path: str
    artifacts_path: str
    observability_enabled: bool
    qualification_required: bool
    deterministic_mode: bool
    ops_policy_id: str
    ops_policy_version: str
    content_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class ObservabilityReadinessEvidence:
    event_schema_version: str
    audit_journal_schema_version: str
    event_identity_validation_available: bool
    audit_chain_validation_available: bool
    diagnostic_logging_available: bool
    sensitive_data_guard_available: bool
    content_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class QualificationReference:
    """Pinned producer identity plus the original result; never a bare PASSED flag."""

    result: QualificationSuiteResult
    content_identity: ContentIdentity
    qualification_run_id: str


@dataclass(frozen=True, slots=True)
class OperationalHealthEvidence:
    config_loaded: bool | None
    internal_components_available: bool | None
    observability_functional: bool | None
    fatal_error_present: bool | None
    content_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class StartupCheckResult:
    check: StartupCheck
    status: CheckStatus
    reason_code: OperationalReasonCode
    critical: bool
    evidence_identity: ContentIdentity | None

    @property
    def content_identity(self) -> ContentIdentity:
        return identity({f.name: getattr(self, f.name) for f in fields(self)})


@dataclass(frozen=True, slots=True)
class OperationalReadinessResult:
    environment: OperationalEnvironment | None
    readiness_status: ReadinessStatus
    reason_code: OperationalReasonCode
    config_identity: ContentIdentity
    ops_policy_identity: ContentIdentity
    startup_checks: tuple[StartupCheckResult, ...]
    qualification_result_identity: ContentIdentity | None
    observability_evidence_identity: ContentIdentity | None
    config: OperationalConfig | None

    content_identity: ContentIdentity = dataclass_field(init=False)

    def __post_init__(self) -> None:
        expected = self.recompute_content_identity()
        if hasattr(self, "content_identity"):
            if self.content_identity != expected:
                raise ValidationError("Stored result identity is inconsistent")
        else:
            object.__setattr__(self, "content_identity", expected)

    def recompute_content_identity(self) -> ContentIdentity:
        return identity(
            {
                "environment": self.environment,
                "readiness_status": self.readiness_status,
                "reason_code": self.reason_code,
                "config_identity": self.config_identity,
                "ops_policy_identity": self.ops_policy_identity,
                "checks": [
                    str(c.content_identity)
                    for c in sorted(self.startup_checks, key=lambda c: c.check)
                ],
                "qualification": self.qualification_result_identity,
                "observability": self.observability_evidence_identity,
            }
        )


@dataclass(frozen=True, slots=True)
class ShutdownRequest:
    reason: ShutdownReason
    requested_from_state: OperationalState
    causal_evidence_identity: ContentIdentity | None
    content_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    status: ShutdownStatus
    reason_code: OperationalReasonCode
    previous_state: OperationalState | None
    resulting_state: OperationalState | None
    shutdown_reason: ShutdownReason | None
    causal_evidence_identity: ContentIdentity | None
    input_identity: ContentIdentity

    @property
    def content_identity(self) -> ContentIdentity:
        return identity({f.name: getattr(self, f.name) for f in fields(self)})
