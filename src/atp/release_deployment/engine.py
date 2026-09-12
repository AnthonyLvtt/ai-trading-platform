"""Release inspection and promotion. Pure decisions; deployment IO is separate."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from base64 import urlsafe_b64encode
from pathlib import PurePosixPath

from atp.release_deployment.model import (
    ALLOWED,
    FORBIDDEN,
    BuildEvidence,
    CandidateResult,
    EvidenceType,
    PromotionDecision,
    PromotionStatus,
    QualificationProof,
    Reason,
    ReleaseBundle,
    ReleaseCandidate,
    ReleaseError,
    ReleaseEvidence,
    ReleaseManifest,
    ReleasePolicy,
    ReleaseStatus,
    SourceTree,
    Target,
    ValidationEvidence,
    encode,
    subject_source,
    verify,
)
from atp.shared.identity import ContentIdentity
from atp.test_qualification import CASES_V1, SUITE_V1, evaluate_suite
from atp.test_qualification.inspection import validate_qualification_result
from atp.test_qualification.model import QualificationStatus


def inspect_wheel(name: str, data: bytes) -> str:
    """Check filename, ZIP structure and RECORD hashes without executing package code."""
    if (
        type(name) is not str
        or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name)
        or type(data) is not bytes
    ):
        raise ReleaseError(Reason.RELEASE_ARTIFACT_TAMPERED)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            names = wheel.namelist()
            if len(names) != len(set(names)) or any(
                PurePosixPath(n).is_absolute()
                or ".." in PurePosixPath(n).parts
                or "\\" in n
                or n.endswith("/")
                for n in names
            ):
                raise ValueError()
            records = [n for n in names if n.endswith(".dist-info/RECORD")]
            if len(records) != 1:
                raise ValueError()
            prefix = records[0].rsplit("/", 1)[0]
            if prefix + "/METADATA" not in names or prefix + "/WHEEL" not in names:
                raise ValueError()
            rows = list(csv.reader(io.StringIO(wheel.read(records[0]).decode("utf-8"))))
            if any(len(row) != 3 for row in rows) or len(rows) != len(names):
                raise ValueError()
            if {row[0] for row in rows} != set(names):
                raise ValueError()
            for path, digest, size in rows:
                if path == records[0]:
                    if digest or size:
                        raise ValueError()
                    continue
                content = wheel.read(path)
                expected = "sha256=" + urlsafe_b64encode(
                    hashlib.sha256(content).digest()
                ).decode().rstrip("=")
                if digest != expected or size != str(len(content)):
                    raise ValueError()
        return hashlib.sha256(data).hexdigest()
    except (ValueError, OSError, KeyError, UnicodeError, zipfile.BadZipFile, RuntimeError):
        raise ReleaseError(Reason.RELEASE_ARTIFACT_TAMPERED) from None


def _qualification(proof: object, source: SourceTree) -> QualificationProof:
    if proof is None:
        raise ReleaseError(Reason.QUALIFICATION_REQUIRED)
    try:
        verify(proof, QualificationProof)
        assert isinstance(proof, QualificationProof)
        if not validate_qualification_result(proof.result):
            raise ReleaseError(Reason.QUALIFICATION_INVALID)
        if proof.result.status is not QualificationStatus.PASSED:
            raise ReleaseError(Reason.QUALIFICATION_INVALID)
        if (
            proof.repository_identity != source.repository_identity
            or proof.source_commit_sha != source.source_commit_sha
        ):
            raise ReleaseError(Reason.QUALIFICATION_SOURCE_MISMATCH)
        for subject in proof.subjects:
            data = subject_source(subject)
            if (
                data.get("repository_identity") != str(source.repository_identity)
                or data.get("source_commit_sha") != source.source_commit_sha
            ):
                raise ReleaseError(Reason.QUALIFICATION_SOURCE_MISMATCH)
        inspected = evaluate_suite(SUITE_V1, CASES_V1, proof.evidence, subjects=proof.subjects)
        if inspected != proof.result:
            raise ReleaseError(Reason.QUALIFICATION_INVALID)
        return proof
    except ReleaseError as exc:
        if exc.reason in (Reason.SENSITIVE_DATA_DETECTED, Reason.QUALIFICATION_SOURCE_MISMATCH):
            raise
        raise ReleaseError(Reason.QUALIFICATION_INVALID) from None
    except (ValueError, TypeError, UnicodeError):
        raise ReleaseError(Reason.QUALIFICATION_INVALID) from None


def _manifest(c: ReleaseCandidate) -> ReleaseManifest:
    return ReleaseManifest(
        "1.0",
        "ATP_RELEASE_V1",
        "1.0",
        c.release_candidate_id,
        c.version,
        c.source_commit_sha,
        c.source_branch,
        c.repository_identity,
        c.lockfile_identity,
        c.build_artifact_name,
        c.build_artifact_sha256,
        SUITE_V1.suite_id,
        SUITE_V1.suite_version,
        c.qualification_result_identity,
        c.qualification_run_id,
        ALLOWED,
        FORBIDDEN,
        False,
    )


def create_candidate(
    source: object,
    validation: object,
    build: object,
    qualification: object,
    wheel: object,
    version: object,
    policy: object = None,
) -> CandidateResult:
    try:
        p = ReleasePolicy() if policy is None else policy
        verify(p, ReleasePolicy)
        verify(source, SourceTree)
        assert isinstance(source, SourceTree) and isinstance(p, ReleasePolicy)
        if not source.clean:
            raise ReleaseError(Reason.DIRTY_WORKTREE)
        if (
            source.source_branch != "main"
            or not re.fullmatch(r"[0-9a-f]{40}", source.source_commit_sha)
            or not re.fullmatch(r"[0-9a-f]{40}", source.git_tree_sha)
        ):
            raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
        if source.repository_identity != ContentIdentity.from_canonical(
            encode(source.files)
        ) or len({f.path for f in source.files}) != len(source.files):
            raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
        if source.lockfile_identity is None or source.lockfile_identity != next(
            (f.digest for f in source.files if f.path == "uv.lock"), None
        ):
            raise ReleaseError(Reason.LOCKFILE_MISMATCH)
        verify(validation, ValidationEvidence)
        verify(build, BuildEvidence)
        assert isinstance(validation, ValidationEvidence) and isinstance(build, BuildEvidence)
        for checked in (validation, build):
            if checked.lockfile_identity != source.lockfile_identity:
                raise ReleaseError(Reason.LOCKFILE_MISMATCH)
            if (
                checked.source_commit_sha != source.source_commit_sha
                or checked.repository_identity != source.repository_identity
            ):
                raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
        if not all(
            (
                validation.lint_passed,
                validation.typecheck_passed,
                validation.tests_passed,
                validation.lock_check_passed,
                build.successful,
            )
        ):
            raise ReleaseError(Reason.BUILD_FAILED)
        q = _qualification(qualification, source)
        if type(version) is not str or not version or not version.isascii() or len(version) > 64:
            raise ReleaseError(Reason.INVALID_RELEASE_INPUT)
        if type(wheel) is not bytes:
            raise ReleaseError(Reason.RELEASE_ARTIFACT_TAMPERED)
        if inspect_wheel(build.artifact_name, wheel) != build.artifact_sha256:
            raise ReleaseError(Reason.RELEASE_ARTIFACT_TAMPERED)
        c = ReleaseCandidate(
            source.source_commit_sha,
            source.source_branch,
            source.repository_identity,
            source.lockfile_identity,
            build.artifact_name,
            build.artifact_sha256,
            q.result.content_identity,
            q.result.qualification_run_id,
            p.content_identity,
            version,
            ReleaseStatus.QUALIFIED,
        )
        verify(c, ReleaseCandidate)
        evidence = tuple(
            ReleaseEvidence(kind, source.repository_identity, artifact)
            for kind, artifact in (
                (EvidenceType.SOURCE_TREE, source.content_identity),
                (EvidenceType.LOCKFILE, source.lockfile_identity),
                (EvidenceType.BUILD_ARTIFACT, build.content_identity),
                (EvidenceType.QUALIFICATION, q.content_identity),
                (EvidenceType.VALIDATION, validation.content_identity),
            )
        )
        return CandidateResult(
            ReleaseStatus.QUALIFIED,
            Reason.RELEASE_READY,
            ReleaseBundle(c, _manifest(c), source, validation, build, q, evidence),
        )
    except ReleaseError as exc:
        return CandidateResult(ReleaseStatus.BLOCKED, exc.reason)


def inspect_bundle(bundle: object, wheel: object, policy: object = None) -> Reason | None:
    try:
        if type(bundle) is not ReleaseBundle:
            return Reason.INVALID_RELEASE_INPUT
        try:
            verify(bundle.manifest, ReleaseManifest)
        except ReleaseError as exc:
            return (
                exc.reason
                if exc.reason is Reason.SENSITIVE_DATA_DETECTED
                else Reason.RELEASE_MANIFEST_INVALID
            )
        verify(bundle, ReleaseBundle)
        rebuilt = create_candidate(
            bundle.source,
            bundle.validation,
            bundle.build,
            bundle.qualification,
            wheel,
            bundle.candidate.version,
            policy,
        )
        if rebuilt.bundle is None:
            return rebuilt.reason_code
        if bundle.manifest != rebuilt.bundle.manifest:
            return Reason.RELEASE_MANIFEST_INVALID
        if bundle != rebuilt.bundle:
            return Reason.INVALID_RELEASE_INPUT
        return None
    except (AttributeError, ReleaseError) as exc:
        return exc.reason if isinstance(exc, ReleaseError) else Reason.INVALID_RELEASE_INPUT


def promote(
    bundle: object, wheel: object, target: object, policy: object = None
) -> PromotionDecision:
    p = ReleasePolicy()
    selected = target if type(target) is Target else None
    reason = (
        {
            Target.LIVE: Reason.LIVE_FORBIDDEN,
            Target.TESTNET: Reason.TESTNET_NOT_AUTHORIZED,
            Target.DRY_RUN: Reason.PROMOTION_TARGET_FORBIDDEN,
        }.get(selected)
        if selected is not None
        else None
    )
    if selected is None:
        reason = Reason.PROMOTION_TARGET_FORBIDDEN
    if reason is None:
        reason = inspect_bundle(bundle, wheel, policy)
    good = reason is None
    c = bundle.candidate if good and isinstance(bundle, ReleaseBundle) else None
    m = bundle.manifest if good and isinstance(bundle, ReleaseBundle) else None
    return PromotionDecision(
        selected,
        PromotionStatus.ALLOWED if good else PromotionStatus.BLOCKED,
        Reason.PROMOTION_ALLOWED if good else reason or Reason.INVALID_RELEASE_INPUT,
        None if c is None else c.content_identity,
        None if m is None else m.content_identity,
        p.content_identity,
    )
