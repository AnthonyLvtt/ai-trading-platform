"""Immutable input references and closed JSON response contracts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from atp.shared.identity import ContentIdentity


class WebReasonCode(StrEnum):
    WEB_OK = "WEB_OK"
    ARTIFACT_NOT_AVAILABLE = "ARTIFACT_NOT_AVAILABLE"
    INVALID_WEB_ARTIFACT = "INVALID_WEB_ARTIFACT"
    ARTIFACT_INTEGRITY_FAILURE = "ARTIFACT_INTEGRITY_FAILURE"
    SENSITIVE_DATA_DETECTED = "SENSITIVE_DATA_DETECTED"
    UNSUPPORTED_ARTIFACT_VERSION = "UNSUPPORTED_ARTIFACT_VERSION"
    WEB_STATE_INCONSISTENT = "WEB_STATE_INCONSISTENT"


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Pinned at trusted composition; the fingerprint covers even non-identity fields."""

    artifact: object
    content_identity: ContentIdentity
    fingerprint: ContentIdentity
    causal_input: object = None


@dataclass(frozen=True, slots=True)
class HealthResult:
    """OPS-produced status and its evidence, supplied together by the caller."""

    health_status: object
    evidence: object


@dataclass(frozen=True, slots=True)
class WebState:
    health_result: ArtifactReference | None = None
    readiness_result: ArtifactReference | None = None
    qualification_result: ArtifactReference | None = None
    audit_journal: ArtifactReference | None = None
    backtest_results: tuple[ArtifactReference, ...] = ()
    accounting_replay_results: tuple[ArtifactReference, ...] = ()
    accounting_valuations: tuple[ArtifactReference, ...] = ()

    @property
    def content_identity(self) -> ContentIdentity:
        from atp.web.validation import fingerprint

        return fingerprint(self)


class View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    status: Literal["OK"] = "OK"
    view_identity: str


class HealthView(View):
    health_status: Literal["HEALTHY", "UNHEALTHY", "UNKNOWN"]
    evidence_identity: str


class ReadinessView(View):
    readiness_status: Literal["READY", "BLOCKED"]
    reason_code: str
    environment: str | None
    config_identity: str
    qualification_result_identity: str | None
    observability_evidence_identity: str | None
    readiness_identity: str


class CaseSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    case_id: str
    status: str
    reason_code: str
    content_identity: str


class QualificationView(View):
    qualification_status: str
    reason_code: str
    suite_id: str
    suite_version: str
    qualification_run_id: str
    case_summary: list[CaseSummary]
    qualification_identity: str


class AuditSummaryView(View):
    event_count: int
    first_event_identity: str | None
    last_event_identity: str | None
    journal_identity: str
    valid_integrity: bool


class BacktestSummaryView(View):
    backtest_status: str
    reason_code: str | None
    replay_id: str
    input_identity: str
    result_identity: str
    orders_count: int
    fills_count: int
    blocked_count: int
    unfilled_count: int
    latest_causal_time: str


class AccountingSummaryView(View):
    source: Literal["VALUATION", "REPLAY"]
    accounting_status: str
    reason_code: str | None
    currency: Literal["USDT"]
    cash: str
    cumulative_realized_pnl: str
    unrealized_pnl: str | None = None
    equity: str | None = None
    valuation_time: str | None = None
    accounting_identity: str | None = None
    position_state: str | None = None
    replay_identity: str | None = None


class WebError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["BLOCKED"] = "BLOCKED"
    reason_code: WebReasonCode
    resource: str
    content_identity: str | None = None
