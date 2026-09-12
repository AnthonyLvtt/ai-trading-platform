import base64
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from dataclasses import replace

import pytest

from atp.release_deployment import create_candidate, deploy, plan_deployment, promote
from atp.release_deployment.engine import inspect_bundle
from atp.release_deployment.model import (
    BuildEvidence,
    DeploymentStatus,
    InstallMode,
    PromotionStatus,
    QualificationProof,
    Reason,
    ReleaseError,
    ReleasePolicy,
    ReleaseStatus,
    Target,
    ValidationEvidence,
)
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.test_qualification import (
    CASES_V1,
    SUITE_V1,
    QualificationSubject,
    build_evidence,
    evaluate_suite,
)


def wheel_fixture():
    entries = {
        "fixture/__init__.py": b"# inert package\n",
        "fixture-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: fixture\nVersion: 1.0\n",
        "fixture-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, data in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow(("fixture-1.0.dist-info/RECORD", "", ""))
    entries["fixture-1.0.dist-info/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in entries.items():
            archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data)
    return output.getvalue()


def qualification(source):
    subjects, evidence = [], []
    for case in CASES_V1:
        observations = {
            check: {
                "case_id": case.case_id,
                "check_id": check,
                "satisfied": True,
                "reproducible": True,
                "applicable": True,
            }
            for check in case.required_checks
        }
        subject = QualificationSubject(
            case.case_id,
            case.source_module,
            "release-contract-fixture",
            canonical_json_bytes(
                {
                    "repository_identity": str(source.repository_identity),
                    "source_commit_sha": source.source_commit_sha,
                    "observations": observations,
                }
            ),
        )
        subjects.append(subject)
        for check in case.required_checks:
            built = build_evidence(
                evidence_id=f"{case.case_id}:{check}",
                case=case,
                check_id=check,
                subject=subject,
                payload={
                    "satisfied": True,
                    "reproducible": True,
                    "applicable": True,
                    "observation_id": check,
                },
            )
            assert built.evidence is not None
            evidence.append(built.evidence)
    result = evaluate_suite(SUITE_V1, CASES_V1, tuple(evidence), subjects=tuple(subjects))
    return QualificationProof(
        source.source_commit_sha,
        source.repository_identity,
        result,
        tuple(subjects),
        tuple(evidence),
    )


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Release fixture")
    (root / "uv.lock").write_text("version = 1\n")
    (root / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "1.0"\n')
    git("add", "uv.lock", "pyproject.toml")
    git("commit", "-m", "fixture source")
    source = inspect_source(root)
    wheel = wheel_fixture()
    validation = ValidationEvidence(
        source.source_commit_sha,
        source.repository_identity,
        source.lockfile_identity,
        True,
        True,
        True,
        True,
    )
    build = BuildEvidence(
        source.source_commit_sha,
        source.repository_identity,
        source.lockfile_identity,
        "fixture-1.0-py3-none-any.whl",
        hashlib.sha256(wheel).hexdigest(),
        True,
    )
    return [source, validation, build, qualification(source), wheel, "internal-001"]


def qualified(inputs):
    result = create_candidate(*inputs)
    assert result.status is ReleaseStatus.QUALIFIED, result.reason_code
    return result.bundle


def test_deterministic_candidate_manifest_and_version(inputs):
    first = qualified(inputs)
    assert first == qualified(inputs)
    assert json.loads(first.manifest.to_json())["content_identity"] == str(
        first.manifest.content_identity
    )
    inputs[-1] = "v1-rc1"
    assert qualified(inputs).candidate.content_identity != first.candidate.content_identity


@pytest.mark.parametrize(
    "index,field,value,reason",
    [
        (0, "clean", False, Reason.DIRTY_WORKTREE),
        (0, "source_branch", "feature", Reason.INVALID_SOURCE_COMMIT),
        (0, "lockfile_identity", None, Reason.LOCKFILE_MISMATCH),
        (2, "lockfile_identity", ContentIdentity.from_bytes(b"other"), Reason.LOCKFILE_MISMATCH),
        (2, "successful", False, Reason.BUILD_FAILED),
        (1, "tests_passed", False, Reason.BUILD_FAILED),
        (
            3,
            "repository_identity",
            ContentIdentity.from_bytes(b"other"),
            Reason.QUALIFICATION_SOURCE_MISMATCH,
        ),
    ],
)
def test_invalid_evidence_blocks(inputs, index, field, value, reason):
    inputs[index] = replace(inputs[index], **{field: value})
    result = create_candidate(*inputs)
    assert result.status is ReleaseStatus.BLOCKED
    assert result.reason_code is reason


def test_missing_and_tampered_qualification(inputs):
    proof = inputs[3]
    inputs[3] = None
    assert create_candidate(*inputs).reason_code is Reason.QUALIFICATION_REQUIRED
    inputs[3] = proof
    object.__setattr__(proof.result, "suite_version", "2.0")
    assert create_candidate(*inputs).reason_code is Reason.QUALIFICATION_INVALID


def test_relabeling_qualification_does_not_change_its_subject_source(inputs):
    proof = inputs[3]
    wrong = replace(
        proof.subjects[0],
        content=proof.subjects[0].content.replace(inputs[0].source_commit_sha.encode(), b"0" * 40),
    )
    inputs[3] = replace(proof, subjects=(wrong,) + proof.subjects[1:])
    assert create_candidate(*inputs).reason_code is Reason.QUALIFICATION_SOURCE_MISMATCH


@pytest.mark.parametrize("target", list(Target))
def test_promotion_matrix(inputs, target):
    bundle = qualified(inputs)
    result = promote(bundle, inputs[4], target)
    reasons = {
        Target.LIVE: Reason.LIVE_FORBIDDEN,
        Target.TESTNET: Reason.TESTNET_NOT_AUTHORIZED,
        Target.DRY_RUN: Reason.PROMOTION_TARGET_FORBIDDEN,
    }
    assert result.reason_code is reasons.get(target, Reason.PROMOTION_ALLOWED)
    assert result.status is (
        PromotionStatus.BLOCKED if target in reasons else PromotionStatus.ALLOWED
    )


def test_manifest_and_wheel_tampering(inputs):
    bundle = qualified(inputs)
    assert inspect_bundle(bundle, inputs[4] + b"x") is Reason.RELEASE_ARTIFACT_TAMPERED
    object.__setattr__(bundle.manifest, "version", "forged")
    assert inspect_bundle(bundle, inputs[4]) is Reason.RELEASE_MANIFEST_INVALID


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "2"),
        ("live_promotion_allowed", True),
        ("source_branch", "feature"),
        ("secrets_allowed", True),
    ],
)
def test_policy_locked_and_revalidated(inputs, field, value):
    with pytest.raises(ReleaseError):
        ReleasePolicy(**{field: value})
    policy = ReleasePolicy()
    object.__setattr__(policy, field, value)
    assert create_candidate(*inputs, policy=policy).status is ReleaseStatus.BLOCKED


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("bad", [None, "", 42, [], object()])
def test_malformed_runtime(inputs, index, bad):
    inputs[index] = bad
    first = create_candidate(*inputs)
    assert first.status is ReleaseStatus.BLOCKED
    assert first == create_candidate(*inputs)


def test_secret_not_echoed(inputs):
    inputs[-1] = "api_key=fixture-sensitive-marker"
    result = create_candidate(*inputs)
    assert result.reason_code is Reason.SENSITIVE_DATA_DETECTED
    assert "fixture-sensitive-marker" not in repr(result)


@pytest.mark.parametrize("mode", list(InstallMode))
def test_real_local_deployment_never_starts_process(inputs, tmp_path, mode):
    bundle = qualified(inputs)
    target = tmp_path / "installed"
    plan = plan_deployment(bundle, inputs[4], Target.LOCAL, str(target), mode)
    assert plan.plan is not None
    result = deploy(bundle, inputs[4], plan.plan)
    assert result.status is DeploymentStatus.COMPLETED, result.reason_code
    assert result.process_started is False
    assert (
        hashlib.sha256((target / bundle.candidate.build_artifact_name).read_bytes()).hexdigest()
        == result.installed_artifact_sha256
    )
    if mode is InstallMode.PYTHON_WHEEL_INSTALL:
        assert (target / "fixture/__init__.py").exists()
    assert deploy(bundle, inputs[4], plan.plan).status is DeploymentStatus.BLOCKED


def test_autostart_remote_and_tampered_plan_blocked(inputs, tmp_path):
    bundle = qualified(inputs)
    for path in ["ssh://host/a", "s3://bucket/a", "host:/a", "//server/a"]:
        assert plan_deployment(bundle, inputs[4], Target.LOCAL, path).plan is None
    assert (
        plan_deployment(bundle, inputs[4], Target.LOCAL, str(tmp_path), auto_start=True).plan
        is None
    )
    plan = plan_deployment(bundle, inputs[4], Target.LOCAL, str(tmp_path / "target")).plan
    object.__setattr__(plan, "auto_start", True)
    assert deploy(bundle, inputs[4], plan).status is DeploymentStatus.BLOCKED
    assert not (tmp_path / "target").exists()


def test_source_dirty_and_changed_source_identity(inputs, tmp_path):
    root = tmp_path / "repo"
    before = inspect_source(root)
    (root / "pyproject.toml").write_text("changed")
    after = inspect_source(root)
    assert after.clean is False
    assert before.repository_identity != after.repository_identity
    inputs[0] = after
    assert create_candidate(*inputs).reason_code is Reason.DIRTY_WORKTREE


def test_changed_committed_source_requires_new_qualification(inputs, tmp_path):
    previous = qualified(inputs).candidate.content_identity
    root = tmp_path / "repo"
    (root / "pyproject.toml").write_text('[project]\nname = "changed"\nversion = "1.0"\n')
    subprocess.run(
        ["git", "-C", str(root), "commit", "-am", "changed"], check=True, capture_output=True
    )
    source = inspect_source(root)
    inputs[0] = source
    for index in (1, 2):
        inputs[index] = replace(
            inputs[index],
            source_commit_sha=source.source_commit_sha,
            repository_identity=source.repository_identity,
        )
    assert create_candidate(*inputs).reason_code is Reason.QUALIFICATION_SOURCE_MISMATCH
    inputs[3] = qualification(source)
    assert qualified(inputs).candidate.content_identity != previous


@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "files", ["bad"]),
        (1, "tests_passed", "true"),
        (2, "artifact_name", object()),
        (3, "subjects", [None]),
    ],
)
def test_malformed_nested_evidence_blocks(inputs, index, field, value):
    object.__setattr__(inputs[index], field, value)
    assert create_candidate(*inputs).status is ReleaseStatus.BLOCKED


def test_no_external_repr_is_called(inputs):
    class Hostile:
        def __str__(self):
            raise AssertionError("untrusted conversion")

        __repr__ = __str__

    inputs[0] = Hostile()
    assert create_candidate(*inputs).status is ReleaseStatus.BLOCKED
    assert promote(Hostile(), Hostile(), Target.LOCAL).status is PromotionStatus.BLOCKED
    assert deploy(Hostile(), Hostile(), Hostile()).status is DeploymentStatus.BLOCKED
