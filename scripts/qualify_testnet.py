"""Offline TQ collector. Runs explicit probes, never constructs a transport."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from atp.exchange.model import ExchangePolicy
from atp.exchange.read_only import encoded
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.testnet_qualification import qualify
from atp.testnet_qualification.model import (
    CASES,
    ExchangeContractEvidence,
    ProbeObservation,
    TestnetCapabilityEvidence,
    TestnetQualificationPolicy,
)


class Reports:
    def __init__(self):
        self.phases = {}
        self.subjects = {}

    def pytest_runtest_logreport(self, report):
        self.phases.setdefault(report.nodeid, {})[report.when] = report.outcome
        self.subjects[report.nodeid] = tuple(
            sorted(set(value for name, value in report.user_properties if name == "tq_subject"))
        )


def collect(output: Path) -> int:
    root = Path(__file__).resolve().parents[1]
    if Path.cwd() != root:
        raise SystemExit("Run from repository root")
    before = inspect_source(root)
    reports = Reports()
    code = pytest.main(
        ["tests/unit/test_testnet_qualification.py", "tests/contract/test_testnet_shadow.py", "-q"],
        plugins=[reports],
    )
    after = inspect_source(root)
    if before != after:
        raise SystemExit("SOURCE_CHANGED_DURING_QUALIFICATION")
    contracts = []
    for case in CASES:
        observations = []
        for node, phases in sorted(reports.phases.items()):
            name = node.split("::")[-1].split("[")[0]
            if name not in case.probes:
                continue
            references = tuple(
                ContentIdentity(*v.split(":", 1)) for v in reports.subjects.get(node, ())
            )
            # Static boundary/security probes refer to the exact inspected source inventory.
            if not references:
                references = (after.repository_identity,)
            observations.append(
                ProbeObservation(
                    name,
                    node,
                    phases.get("setup", "missing"),
                    phases.get("call", "missing"),
                    phases.get("teardown", "missing"),
                    references,
                )
            )
        contracts.append(
            ExchangeContractEvidence(
                case.case_id,
                after.source_commit_sha,
                after.repository_identity,
                ExchangePolicy().content_identity,
                TestnetQualificationPolicy().content_identity,
                tuple(observations),
            )
        )
    bundle = TestnetCapabilityEvidence(
        after.source_commit_sha, after.repository_identity, tuple(contracts)
    )
    result = qualify(bundle)
    document = dict(encoded(result)) | {
        "adapter_policy_version": ExchangePolicy().version,
        "level": "OFFLINE_CONTRACT",
        "case_result_identities": [str(case.content_identity) for case in result.cases],
        "content_identity": str(result.content_identity),
        "evidence": encoded(bundle),
        "evidence_identity": str(bundle.content_identity),
    }
    output.write_bytes(canonical_json_bytes(document))
    print(result.status.value, str(result.content_identity))
    return 0 if result.status.value == "PASSED" and code == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("testnet-qualification.json"))
    raise SystemExit(collect(parser.parse_args().output))
