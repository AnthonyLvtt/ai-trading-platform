import ast
from pathlib import Path

from atp.release_deployment import create_candidate, deploy, plan_deployment, promote
from atp.release_deployment.model import DeploymentStatus, Reason, Target
from tests.unit.test_release_deployment import inputs  # noqa: F401


def test_source_qualification_wheel_to_local_deployment(inputs, tmp_path):  # noqa: F811
    result = create_candidate(*inputs)
    assert result.bundle is not None
    assert len(result.bundle.qualification.result.case_results) == 10
    assert len(result.bundle.evidence) == 5
    assert result.bundle.candidate.repository_identity == inputs[0].repository_identity
    assert (
        result.bundle.candidate.qualification_result_identity == inputs[3].result.content_identity
    )
    plan = plan_deployment(result.bundle, inputs[4], Target.LOCAL, str(tmp_path / "deployment"))
    assert plan.plan is not None
    deployed = deploy(result.bundle, inputs[4], plan.plan)
    assert deployed.status is DeploymentStatus.COMPLETED
    assert deployed.process_started is False
    for target, reason in [
        (Target.TESTNET, Reason.TESTNET_NOT_AUTHORIZED),
        (Target.LIVE, Reason.LIVE_FORBIDDEN),
    ]:
        assert promote(result.bundle, inputs[4], target).reason_code is reason


def test_release_authority_and_network_boundaries():
    forbidden = {
        "oms",
        "exchange",
        "strategy",
        "risk",
        "accounting",
        "ops",
        "ai",
        "ml",
        "requests",
        "httpx",
        "urllib",
        "socket",
    }
    for path in Path("src/atp/release_deployment").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(set(name.split(".")) & forbidden for name in names), path
    deployment = Path("src/atp/release_deployment/deployment.py").read_text()
    assert '"--offline"' in deployment and '"--no-index"' in deployment
    assert '"--no-deps"' in deployment
    workflow = Path(".github/workflows/release.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "secrets." not in workflow
    assert "upload-artifact" in workflow
