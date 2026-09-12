"""Explicit main-only local release producer; never deploys or starts a runtime."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from qualify import collect

from atp.release_deployment.engine import create_candidate, inspect_bundle
from atp.release_deployment.model import (
    BuildEvidence,
    QualificationProof,
    Reason,
    ReleaseError,
    SourceTree,
    ValidationEvidence,
)
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity


def materialize_source(root: Path, destination: Path, source: SourceTree) -> None:
    """Build only the qualified tracked bytes, excluding ignored/untracked local files."""
    destination.mkdir()
    for entry in source.files:
        relative = Path(entry.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
        content = (root / relative).read_bytes()
        if ContentIdentity.from_bytes(content) != entry.digest:
            raise ReleaseError(Reason.DIRTY_WORKTREE)
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        output.chmod(0o755 if entry.git_mode == "100755" else 0o644)


def run(root: Path, version: str, output: Path, commit: str) -> int:
    try:
        before = inspect_source(root)
        if before.source_commit_sha != commit or before.source_branch != "main":
            raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
        if not before.clean:
            raise ReleaseError(Reason.DIRTY_WORKTREE)
        if before.lockfile_identity is None:
            raise ReleaseError(Reason.LOCKFILE_MISMATCH)
        lock_check = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if lock_check.returncode:
            raise ReleaseError(Reason.LOCKFILE_MISMATCH)
        destination = output.resolve()
        if destination == root or root in destination.parents or destination.exists():
            raise ReleaseError(Reason.DEPLOYMENT_PLAN_INVALID)
        with tempfile.TemporaryDirectory(prefix="atp-release-build-") as temporary:
            stage = Path(temporary)
            validation = subprocess.run(
                ["uv", "run", "--frozen", "make", "validate"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            if validation.returncode:
                raise ReleaseError(Reason.BUILD_FAILED)
            # Collector emits its conventional JSON outside the source tree for this run.
            q, subjects, evidence, qualified_source = collect(
                output_path=stage / "qualification.json"
            )
            if qualified_source != before:
                raise ReleaseError(Reason.QUALIFICATION_SOURCE_MISMATCH)
            build_source = stage / "source"
            materialize_source(root, build_source, before)
            build = subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(stage)],
                cwd=build_source,
                env=os.environ | {"SOURCE_DATE_EPOCH": "315532800"},
                capture_output=True,
                check=False,
            )
            if build.returncode:
                raise ReleaseError(Reason.BUILD_FAILED)
            after = inspect_source(root)
            if after != before:
                raise ReleaseError(Reason.DIRTY_WORKTREE)
            wheels = tuple(stage.glob("*.whl"))
            if len(wheels) != 1:
                raise ReleaseError(Reason.BUILD_FAILED)
            artifact = wheels[0]
            wheel = artifact.read_bytes()
            ve = ValidationEvidence(
                commit,
                before.repository_identity,
                before.lockfile_identity,
                True,
                True,
                True,
                lock_check.returncode == 0,
            )
            be = BuildEvidence(
                commit,
                before.repository_identity,
                before.lockfile_identity,
                artifact.name,
                hashlib.sha256(wheel).hexdigest(),
                True,
            )
            qe = QualificationProof(commit, before.repository_identity, q, subjects, evidence)
            result = create_candidate(before, ve, be, qe, wheel, version)
            if result.bundle is None:
                raise ReleaseError(result.reason_code)
            error = inspect_bundle(result.bundle, wheel)
            if error is not None:
                raise ReleaseError(error)
            destination.mkdir(parents=True)
            (destination / artifact.name).write_bytes(wheel)
            (destination / "release-manifest.json").write_bytes(result.bundle.manifest.to_json())
            (destination / "qualification.json").write_bytes(
                (stage / "qualification.json").read_bytes()
            )
            print("QUALIFIED", result.bundle.candidate.release_candidate_id)
        return 0
    except (ReleaseError, OSError) as exc:
        print(
            "BLOCKED",
            exc.reason.value if isinstance(exc, ReleaseError) else Reason.BUILD_FAILED.value,
        )
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(run(Path.cwd().resolve(), args.version, args.output, args.source_commit))
