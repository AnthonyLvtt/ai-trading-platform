"""Explicit local copy/install boundary; never starts ATP or changes OPS configuration."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from atp.release_deployment.engine import promote
from atp.release_deployment.model import (
    DeploymentPlan,
    DeploymentResult,
    DeploymentStatus,
    InstallMode,
    PlanResult,
    PromotionStatus,
    Reason,
    ReleaseBundle,
    ReleaseError,
    Target,
    sensitive,
    verify,
)


def _target(value: object) -> Path:
    if (
        type(value) is not str
        or not value
        or ":" in value
        or "@" in value
        or "\\" in value
        or value.startswith("//")
        or not Path(value).is_absolute()
    ):
        raise ReleaseError(Reason.DEPLOYMENT_PLAN_INVALID)
    if sensitive(value):
        raise ReleaseError(Reason.SENSITIVE_DATA_DETECTED)
    return Path(value).resolve()


def plan_deployment(
    bundle: object,
    wheel: object,
    target: object,
    local_target_path: object,
    install_mode: object = InstallMode.COPY_ARTIFACT,
    auto_start: object = False,
) -> PlanResult:
    try:
        decision = promote(bundle, wheel, target)
        if decision.status is not PromotionStatus.ALLOWED:
            return PlanResult(decision.reason_code, None)
        if type(install_mode) is not InstallMode or auto_start is not False:
            raise ReleaseError(Reason.DEPLOYMENT_PLAN_INVALID)
        path = _target(local_target_path)
        assert isinstance(bundle, ReleaseBundle) and isinstance(target, Target)
        return PlanResult(
            Reason.PROMOTION_ALLOWED,
            DeploymentPlan(
                target,
                bundle.candidate.content_identity,
                bundle.manifest.content_identity,
                bundle.candidate.build_artifact_name,
                bundle.candidate.build_artifact_sha256,
                str(path),
                install_mode,
                False,
            ),
        )
    except (ReleaseError, OSError, ValueError) as exc:
        return PlanResult(
            exc.reason if isinstance(exc, ReleaseError) else Reason.DEPLOYMENT_PLAN_INVALID, None
        )


def deploy(bundle: object, wheel: object, plan: object) -> DeploymentResult:
    try:
        verify(plan, DeploymentPlan)
        assert isinstance(plan, DeploymentPlan)
        rebuilt = plan_deployment(
            bundle,
            wheel,
            plan.target_environment,
            plan.local_target_path,
            plan.install_mode,
            plan.auto_start,
        )
        if rebuilt.plan is None:
            raise ReleaseError(rebuilt.reason_code)
        if rebuilt.plan != plan:
            raise ReleaseError(Reason.DEPLOYMENT_PLAN_INVALID)
        assert isinstance(wheel, bytes)
        target = _target(plan.local_target_path)
        if str(target) != plan.local_target_path or target.exists() or not target.parent.is_dir():
            raise ReleaseError(Reason.DEPLOYMENT_PLAN_INVALID)
        # Create a fresh destination atomically; never replace an existing environment/config.
        with tempfile.TemporaryDirectory(prefix="atp-release-", dir=target.parent) as temporary:
            stage = Path(temporary) / "install"
            stage.mkdir()
            artifact = stage / plan.artifact_name
            artifact.write_bytes(wheel)
            if plan.install_mode is InstallMode.PYTHON_WHEEL_INSTALL:
                result = subprocess.run(
                    [
                        "uv",
                        "--no-config",
                        "--offline",
                        "pip",
                        "install",
                        "--no-index",
                        "--no-deps",
                        "--target",
                        str(stage),
                        str(artifact),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode:
                    raise ReleaseError(Reason.DEPLOYMENT_BLOCKED)
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != plan.artifact_sha256:
                raise ReleaseError(Reason.RELEASE_ARTIFACT_TAMPERED)
            # Reserve target exclusively, then move files; no overwrite of a caller's directory.
            target.mkdir()
            try:
                for child in stage.iterdir():
                    shutil.move(str(child), str(target / child.name))
            except OSError:
                shutil.rmtree(target)
                raise
        return DeploymentResult(
            DeploymentStatus.COMPLETED,
            Reason.RELEASE_READY,
            plan.target_environment,
            plan.release_candidate_identity,
            plan.content_identity,
            plan.artifact_sha256,
            str(target),
            False,
        )
    except (ReleaseError, OSError, ValueError) as exc:
        return DeploymentResult(
            DeploymentStatus.BLOCKED,
            exc.reason if isinstance(exc, ReleaseError) else Reason.DEPLOYMENT_BLOCKED,
            None,
            None,
            None,
            None,
            None,
            False,
        )
