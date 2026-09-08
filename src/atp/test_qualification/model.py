"""Immutable qualification contracts. These records grant no execution authority."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum

from atp.shared.identity import ContentIdentity


class QualificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class QualificationReasonCode(StrEnum):
    QUALIFICATION_PASSED = "QUALIFICATION_PASSED"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    EVIDENCE_IDENTITY_MISMATCH = "EVIDENCE_IDENTITY_MISMATCH"
    EVIDENCE_PROVENANCE_INCOMPATIBLE = "EVIDENCE_PROVENANCE_INCOMPATIBLE"
    EVIDENCE_NON_DETERMINISTIC = "EVIDENCE_NON_DETERMINISTIC"
    INVARIANT_VIOLATED = "INVARIANT_VIOLATED"
    CASE_DEFINITION_INVALID = "CASE_DEFINITION_INVALID"
    CASE_NOT_APPLICABLE = "CASE_NOT_APPLICABLE"
    SUITE_INCOMPLETE = "SUITE_INCOMPLETE"
    DUPLICATE_CASE = "DUPLICATE_CASE"
    DUPLICATE_EVIDENCE = "DUPLICATE_EVIDENCE"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    SECRET_MATERIAL_DETECTED = "SECRET_MATERIAL_DETECTED"
    MODULE_BOUNDARY_VIOLATION = "MODULE_BOUNDARY_VIOLATION"
    TEMPORAL_CAUSALITY_VIOLATION = "TEMPORAL_CAUSALITY_VIOLATION"
    SECURITY_INVARIANT_VIOLATION = "SECURITY_INVARIANT_VIOLATION"


class EvidenceType(StrEnum):
    VALIDATION_RUN = "VALIDATION_RUN"
    UNIT_TEST_RESULT = "UNIT_TEST_RESULT"
    CONTRACT_TEST_RESULT = "CONTRACT_TEST_RESULT"
    DOMAIN_ARTIFACT = "DOMAIN_ARTIFACT"
    DOMAIN_REPLAY_RESULT = "DOMAIN_REPLAY_RESULT"
    AUDIT_JOURNAL_RESULT = "AUDIT_JOURNAL_RESULT"
    MODULE_BOUNDARY_RESULT = "MODULE_BOUNDARY_RESULT"
    SECURITY_CHECK_RESULT = "SECURITY_CHECK_RESULT"


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    policy_id: str = "ATP_QUALIFICATION_V1"
    version: str = "1.0"
    deterministic: bool = True
    network_required: bool = False
    live_required: bool = False
    exchange_required: bool = False
    secret_material_allowed: bool = False
    missing_evidence: str = "BLOCKED"
    violated_invariant: str = "FAILED"
    satisfied_invariant: str = "PASSED"

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not type(field.default) or value != field.default:
                raise ValueError("qualification policy must be exact V1")

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical({f.name: getattr(self, f.name) for f in fields(self)})


@dataclass(frozen=True, slots=True)
class QualificationCase:
    case_id: str
    case_version: str
    title: str
    invariant_id: str
    required_evidence_types: tuple[EvidenceType, ...]
    required_checks: tuple[str, ...]
    source_module: str
    policy_id: str = "ATP_QUALIFICATION_V1"
    policy_version: str = "1.0"

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical({f.name: getattr(self, f.name) for f in fields(self)})


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    evidence_id: str
    evidence_type: EvidenceType
    source_module: str
    produced_by: str
    case_id: str
    check_id: str
    subject_id: str
    subject_content_identity: ContentIdentity
    policy_id: str
    policy_version: str
    payload: bytes
    content_identity: ContentIdentity

    def canonical_value(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "source_module": self.source_module,
            "produced_by": self.produced_by,
            "case_id": self.case_id,
            "check_id": self.check_id,
            "subject_id": self.subject_id,
            "subject_content_identity": str(self.subject_content_identity),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "payload": self.payload.decode("utf-8"),
        }


@dataclass(frozen=True, slots=True)
class QualificationCaseResult:
    case_id: str
    case_identity: ContentIdentity
    status: QualificationStatus
    reason_code: QualificationReasonCode
    evaluated_evidence_ids: tuple[str, ...]
    evaluated_evidence_identities: tuple[ContentIdentity, ...]
    qualification_policy_identity: ContentIdentity

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(
            {
                "case_id": self.case_id,
                "case_identity": str(self.case_identity),
                "status": self.status.value,
                "reason_code": self.reason_code.value,
                "evidence": list(
                    zip(
                        self.evaluated_evidence_ids,
                        map(str, self.evaluated_evidence_identities),
                        strict=True,
                    )
                ),
                "policy": str(self.qualification_policy_identity),
            }
        )


@dataclass(frozen=True, slots=True)
class QualificationSuite:
    required_case_ids: tuple[str, ...]
    suite_id: str = "ATP_V1_QUALIFICATION"
    suite_version: str = "1.0"
    policy_id: str = "ATP_QUALIFICATION_V1"
    policy_version: str = "1.0"

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical({f.name: getattr(self, f.name) for f in fields(self)})


@dataclass(frozen=True, slots=True)
class QualificationSuiteResult:
    suite_id: str
    suite_version: str
    suite_identity: ContentIdentity
    status: QualificationStatus
    reason_code: QualificationReasonCode
    case_results: tuple[QualificationCaseResult, ...]
    qualification_policy_identity: ContentIdentity

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(
            {
                "suite_id": self.suite_id,
                "suite_version": self.suite_version,
                "suite_identity": str(self.suite_identity),
                "status": self.status.value,
                "reason_code": self.reason_code.value,
                "cases": [str(r.content_identity) for r in self.case_results],
                "policy": str(self.qualification_policy_identity),
            }
        )

    @property
    def qualification_run_id(self) -> str:
        return f"qualification-run:{self.content_identity}"


@dataclass(frozen=True, slots=True)
class QualificationSubject:
    """Explicit canonical source manifest supplied alongside evidence, never fetched remotely."""

    subject_id: str
    source_module: str
    produced_by: str
    content: bytes

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_bytes(self.content)


@dataclass(frozen=True, slots=True)
class EvidenceBuildResult:
    # Successful construction is not a qualification verdict.
    status: QualificationStatus | None
    reason_code: QualificationReasonCode | None
    evidence: QualificationEvidence | None = None
