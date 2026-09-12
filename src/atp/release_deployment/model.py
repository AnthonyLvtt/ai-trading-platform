"""Release V1 records. No record grants runtime execution authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from atp.observability.events import SENSITIVE_KEYS
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.test_qualification.model import (
    QualificationEvidence,
    QualificationSubject,
    QualificationSuiteResult,
)


class Reason(StrEnum):
    RELEASE_READY = "RELEASE_READY"
    PROMOTION_ALLOWED = "PROMOTION_ALLOWED"
    DIRTY_WORKTREE = "DIRTY_WORKTREE"
    INVALID_SOURCE_COMMIT = "INVALID_SOURCE_COMMIT"
    LOCKFILE_MISMATCH = "LOCKFILE_MISMATCH"
    BUILD_FAILED = "BUILD_FAILED"
    QUALIFICATION_REQUIRED = "QUALIFICATION_REQUIRED"
    QUALIFICATION_INVALID = "QUALIFICATION_INVALID"
    QUALIFICATION_SOURCE_MISMATCH = "QUALIFICATION_SOURCE_MISMATCH"
    RELEASE_MANIFEST_INVALID = "RELEASE_MANIFEST_INVALID"
    RELEASE_ARTIFACT_TAMPERED = "RELEASE_ARTIFACT_TAMPERED"
    SENSITIVE_DATA_DETECTED = "SENSITIVE_DATA_DETECTED"
    PROMOTION_TARGET_FORBIDDEN = "PROMOTION_TARGET_FORBIDDEN"
    TESTNET_NOT_AUTHORIZED = "TESTNET_NOT_AUTHORIZED"
    LIVE_FORBIDDEN = "LIVE_FORBIDDEN"
    DEPLOYMENT_PLAN_INVALID = "DEPLOYMENT_PLAN_INVALID"
    DEPLOYMENT_BLOCKED = "DEPLOYMENT_BLOCKED"
    INVALID_RELEASE_INPUT = "INVALID_RELEASE_INPUT"


class ReleaseStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    QUALIFIED = "QUALIFIED"
    BLOCKED = "BLOCKED"


class PromotionStatus(StrEnum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"


class DeploymentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class Target(StrEnum):
    LOCAL = "LOCAL"
    TEST = "TEST"
    BACKTEST = "BACKTEST"
    SIMULATION = "SIMULATION"
    DRY_RUN = "DRY_RUN"
    TESTNET = "TESTNET"
    LIVE = "LIVE"


class InstallMode(StrEnum):
    COPY_ARTIFACT = "COPY_ARTIFACT"
    PYTHON_WHEEL_INSTALL = "PYTHON_WHEEL_INSTALL"


class EvidenceType(StrEnum):
    SOURCE_TREE = "SOURCE_TREE"
    LOCKFILE = "LOCKFILE"
    BUILD_ARTIFACT = "BUILD_ARTIFACT"
    QUALIFICATION = "QUALIFICATION"
    VALIDATION = "VALIDATION"


ALLOWED = (Target.LOCAL, Target.TEST, Target.BACKTEST, Target.SIMULATION)
FORBIDDEN = (Target.DRY_RUN, Target.TESTNET, Target.LIVE)


class ReleaseError(ValueError):
    def __init__(self, reason: Reason):
        super().__init__(reason.value)
        self.reason = reason


def sensitive(value: object, depth: int = 0) -> bool:
    if depth > 30:
        raise ReleaseError(Reason.INVALID_RELEASE_INPUT)
    keys = SENSITIVE_KEYS | {"exchange_secret", "withdrawal"}
    if type(value) is str:
        return bool(
            re.search(r"(?i)(?:" + "|".join(re.escape(k) for k in keys) + r")\s*[:=]", value)
        )
    if type(value) is dict:
        return any(
            type(k) is str and (k.casefold() in keys or sensitive(v, depth + 1))
            for k, v in value.items()
        )
    if isinstance(value, tuple | list):
        return any(sensitive(v, depth + 1) for v in value)
    return False


def encode(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is bytes:
        return value.decode("utf-8")
    if type(value) is ContentIdentity:
        return str(value)
    if type(value) is tuple:
        return [encode(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: encode(getattr(value, f.name))
            for f in fields(value)
            if f.name != "content_identity"
        }
    raise ReleaseError(Reason.INVALID_RELEASE_INPUT)


def identity(value: object) -> ContentIdentity:
    return ContentIdentity.from_canonical(encode(value))


def _typed(value: object, expected: object, depth: int = 0) -> bool:
    if depth > 30:
        return False
    if get_origin(expected) is UnionType:
        return any(_typed(value, t, depth + 1) for t in get_args(expected))
    if get_origin(expected) is tuple:
        args = get_args(expected)
        return (
            type(value) is tuple
            and len(args) == 2
            and args[1] is Ellipsis
            and all(_typed(v, args[0], depth + 1) for v in value)
        )
    if type(value) is not expected:
        return False
    if is_dataclass(value) and not isinstance(value, type):
        hints = get_type_hints(type(value))
        return all(_typed(getattr(value, f.name), hints[f.name], depth + 1) for f in fields(value))
    return True


def verify(value: object, expected: type[object]) -> None:
    try:
        if not _typed(value, expected):
            raise ReleaseError(Reason.INVALID_RELEASE_INPUT)
        _verify_nested(value)
        if sensitive(encode(value)):
            raise ReleaseError(Reason.SENSITIVE_DATA_DETECTED)
    except ReleaseError:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError, ValidationError):
        raise ReleaseError(Reason.INVALID_RELEASE_INPUT) from None


def _verify_nested(value: object) -> None:
    if type(value) is tuple:
        for item in value:
            _verify_nested(item)
    elif is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            _verify_nested(getattr(value, f.name))
        post = getattr(value, "__post_init__", None)
        if post is not None:
            post()


@dataclass(frozen=True, slots=True, kw_only=True)
class Record:
    content_identity: ContentIdentity = field(init=False)

    def __post_init__(self) -> None:
        expected = identity(self)
        if hasattr(self, "content_identity") and self.content_identity != expected:
            raise ReleaseError(Reason.INVALID_RELEASE_INPUT)
        object.__setattr__(self, "content_identity", expected)


@dataclass(frozen=True, slots=True)
class ReleasePolicy(Record):
    policy_id: str = "ATP_RELEASE_V1"
    version: str = "1.0"
    source_branch: str = "main"
    clean_worktree_required: bool = True
    lockfile_required: bool = True
    qualification_required: bool = True
    remote_deployment_allowed: bool = False
    testnet_promotion_allowed: bool = False
    live_promotion_allowed: bool = False
    auto_start_allowed: bool = False
    network_deployment_required: bool = False
    secrets_allowed: bool = False

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name != "content_identity" and (
                type(getattr(self, f.name)) is not type(f.default)
                or getattr(self, f.name) != f.default
            ):
                raise ReleaseError(Reason.INVALID_RELEASE_INPUT)
        Record.__post_init__(self)


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    git_mode: str
    digest: ContentIdentity


@dataclass(frozen=True, slots=True)
class SourceTree(Record):
    source_commit_sha: str
    source_branch: str
    git_tree_sha: str
    repository_identity: ContentIdentity
    lockfile_identity: ContentIdentity | None
    files: tuple[SourceFile, ...]
    clean: bool


@dataclass(frozen=True, slots=True)
class ValidationEvidence(Record):
    source_commit_sha: str
    repository_identity: ContentIdentity
    lockfile_identity: ContentIdentity
    lint_passed: bool
    typecheck_passed: bool
    tests_passed: bool
    lock_check_passed: bool


@dataclass(frozen=True, slots=True)
class BuildEvidence(Record):
    source_commit_sha: str
    repository_identity: ContentIdentity
    lockfile_identity: ContentIdentity
    artifact_name: str
    artifact_sha256: str
    successful: bool


@dataclass(frozen=True, slots=True)
class QualificationProof(Record):
    source_commit_sha: str
    repository_identity: ContentIdentity
    result: QualificationSuiteResult
    subjects: tuple[QualificationSubject, ...]
    evidence: tuple[QualificationEvidence, ...]


@dataclass(frozen=True, slots=True)
class ReleaseEvidence(Record):
    evidence_type: EvidenceType
    source_identity: ContentIdentity
    artifact_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class ReleaseCandidate(Record):
    source_commit_sha: str
    source_branch: str
    repository_identity: ContentIdentity
    lockfile_identity: ContentIdentity
    build_artifact_name: str
    build_artifact_sha256: str
    qualification_result_identity: ContentIdentity
    qualification_run_id: str
    release_policy_identity: ContentIdentity
    version: str
    status: ReleaseStatus

    @property
    def release_candidate_id(self) -> str:
        return f"release-candidate:{self.content_identity}"


@dataclass(frozen=True, slots=True)
class ReleaseManifest(Record):
    schema_version: str
    release_policy_id: str
    release_policy_version: str
    release_candidate_id: str
    version: str
    source_commit_sha: str
    source_branch: str
    repository_identity: ContentIdentity
    lockfile_identity: ContentIdentity
    build_artifact_name: str
    build_artifact_sha256: str
    qualification_suite_id: str
    qualification_suite_version: str
    qualification_result_identity: ContentIdentity
    qualification_run_id: str
    allowed_promotion_targets: tuple[Target, ...]
    forbidden_promotion_targets: tuple[Target, ...]
    auto_start_allowed: bool

    def to_json(self) -> bytes:
        verify(self, ReleaseManifest)
        data = encode(self)
        assert isinstance(data, dict)
        return canonical_json_bytes(data | {"content_identity": str(self.content_identity)})


@dataclass(frozen=True, slots=True)
class ReleaseBundle(Record):
    candidate: ReleaseCandidate
    manifest: ReleaseManifest
    source: SourceTree
    validation: ValidationEvidence
    build: BuildEvidence
    qualification: QualificationProof
    evidence: tuple[ReleaseEvidence, ...]


@dataclass(frozen=True, slots=True)
class CandidateResult(Record):
    status: ReleaseStatus
    reason_code: Reason
    bundle: ReleaseBundle | None = None


@dataclass(frozen=True, slots=True)
class PromotionDecision(Record):
    target: Target | None
    status: PromotionStatus
    reason_code: Reason
    release_candidate_identity: ContentIdentity | None
    manifest_identity: ContentIdentity | None
    policy_identity: ContentIdentity


@dataclass(frozen=True, slots=True)
class DeploymentPlan(Record):
    target_environment: Target
    release_candidate_identity: ContentIdentity
    manifest_identity: ContentIdentity
    artifact_name: str
    artifact_sha256: str
    local_target_path: str
    install_mode: InstallMode
    auto_start: bool


@dataclass(frozen=True, slots=True)
class PlanResult(Record):
    reason_code: Reason
    plan: DeploymentPlan | None


@dataclass(frozen=True, slots=True)
class DeploymentResult(Record):
    status: DeploymentStatus
    reason_code: Reason
    target_environment: Target | None
    release_candidate_identity: ContentIdentity | None
    deployment_plan_identity: ContentIdentity | None
    installed_artifact_sha256: str | None
    resulting_path: str | None
    process_started: bool = False


def subject_source(subject: QualificationSubject) -> dict[str, object]:
    data = json.loads(subject.content)
    if type(data) is not dict or sensitive(data):
        raise ReleaseError(Reason.SENSITIVE_DATA_DETECTED)
    return data
