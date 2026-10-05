"""Offline readiness must not promote absent evidence or touch the campaign."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from atp.exchange.model import ExchangePolicy
from atp.exchange.read_only import encoded
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.model import SubmissionState
from atp.first_testnet_order.readiness_review import (
    ReadinessUnavailable,
    inspect_campaign_read_only,
    review_readiness,
)
from atp.release_deployment.model import SourceTree
from atp.shared.identity import ContentIdentity
from atp.testnet_qualification.model import (
    CASES,
    ExchangeContractEvidence,
    Reason,
    Status,
    TestnetCapabilityEvidence,
    TestnetQualificationPolicy,
    TestnetQualificationResult,
    TestnetQualificationSuiteResult,
)

SHA = "a" * 40


def _source() -> SourceTree:
    return SourceTree(
        SHA,
        "main",
        "b" * 40,
        ContentIdentity.from_text("repository"),
        ContentIdentity.from_text("lock"),
        (),
        True,
    )


def _ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "campaign"
    directory.mkdir(mode=0o700)
    path = directory / "submission.sqlite"
    TestnetSubmissionLedger.create(path)
    return path


def test_empty_canonical_ledger_is_read_only_and_evidence_absence_is_no_go(
    tmp_path: Path,
) -> None:
    path = _ledger(tmp_path)
    before = path.read_bytes()

    def inspector(candidate: Path) -> str:
        return inspect_campaign_read_only(candidate, durable_root=tmp_path)

    first = review_readiness(
        tmp_path,
        SHA,
        ledger_path=path,
        source_inspector=lambda _: _source(),
        ledger_inspector=inspector,
    )
    second = review_readiness(
        tmp_path,
        SHA,
        ledger_path=path,
        source_inspector=lambda _: _source(),
        ledger_inspector=inspector,
    )
    assert first == second
    assert first["status"] == "NO_GO"
    assert first["reason_code"] == "FRESH_RUNTIME_EVIDENCE_REQUIRED"
    assert first["campaign_ledger_state"] == "NOT_ATTEMPTED"
    assert first["source_sha"] == SHA
    assert first["release_identity"] is None
    assert first["credential_reference_identity"] is None
    assert first["first_order_authorization_candidate_identity"] is None
    assert first["quote_cap"] == "6"
    assert first["real_economic_calls"] == 0
    assert first["side_effect_performed"] is False
    assert first["LIVE"] == "LIVE_FORBIDDEN"
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["branch", "sha", "dirty"])
def test_source_mismatch_fails_closed(tmp_path: Path, change: str) -> None:
    source = _source()
    if change == "branch":
        source = replace(source, source_branch="feature")
    elif change == "sha":
        source = replace(source, source_commit_sha="c" * 40)
    else:
        source = replace(source, clean=False)
    report = review_readiness(
        tmp_path,
        SHA,
        source_inspector=lambda _: source,
        ledger_inspector=lambda _: "ledger:synthetic",
    )
    assert report["status"] == "NO_GO"
    assert report["reason_code"] == "SOURCE_BINDING_INVALID"
    assert report["source_tree_identity"] is None


def test_consumed_campaign_blocks_without_modification(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    ledger = TestnetSubmissionLedger(path)
    ledger.append(
        ContentIdentity.from_text("authorization"), "client", SubmissionState.ATTEMPT_STARTED
    )
    before = path.read_bytes()
    with pytest.raises(ReadinessUnavailable, match="FIRST_ORDER_ALREADY_CONSUMED"):
        inspect_campaign_read_only(path, durable_root=tmp_path)
    assert path.read_bytes() == before


def test_schema_permissions_and_symlink_fail_closed(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER no_delete")
    with pytest.raises(ReadinessUnavailable, match="LEDGER_SCHEMA_INVALID"):
        inspect_campaign_read_only(path, durable_root=tmp_path)
    path.chmod(0o644)
    with pytest.raises(ReadinessUnavailable, match="LEDGER_UNAVAILABLE"):
        inspect_campaign_read_only(path, durable_root=tmp_path)
    path.chmod(0o600)
    link = tmp_path / "campaign" / "link.sqlite"
    link.symlink_to(path)
    with pytest.raises(ReadinessUnavailable, match="LEDGER_UNAVAILABLE"):
        inspect_campaign_read_only(link, durable_root=tmp_path)


def test_tq_identity_is_source_bound_but_never_grants_readiness(tmp_path: Path) -> None:
    source = _source()
    adapter = ExchangePolicy().content_identity
    policy = TestnetQualificationPolicy().content_identity
    contracts = tuple(
        ExchangeContractEvidence(case.case_id, SHA, source.repository_identity, adapter, policy, ())
        for case in CASES
    )
    evidence = TestnetCapabilityEvidence(SHA, source.repository_identity, contracts)
    result = TestnetQualificationSuiteResult(
        SHA,
        source.repository_identity,
        adapter,
        policy,
        tuple(case.case_id for case in CASES),
        tuple(
            TestnetQualificationResult(
                case.case_id,
                Status.PASSED,
                Reason.TESTNET_QUALIFICATION_PASSED,
                contract.content_identity,
            )
            for case, contract in zip(CASES, contracts, strict=True)
        ),
        Status.PASSED,
        Reason.TESTNET_QUALIFICATION_PASSED,
    )
    document = dict(encoded(result)) | {
        "content_identity": str(result.content_identity),
        "level": "OFFLINE_CONTRACT",
        "evidence": encoded(evidence),
        "evidence_identity": str(evidence.content_identity),
    }
    path = tmp_path / "tq.json"
    path.write_text(json.dumps(document))
    report = review_readiness(
        tmp_path,
        SHA,
        source_inspector=lambda _: source,
        ledger_inspector=lambda _: "ledger:synthetic",
        tq_result_path=path,
    )
    assert report["tq_identity"] == str(result.content_identity)
    assert report["status"] == "NO_GO"
    document["source_commit_sha"] = "c" * 40
    path.write_text(json.dumps(document))
    tampered = review_readiness(
        tmp_path,
        SHA,
        source_inspector=lambda _: source,
        ledger_inspector=lambda _: "ledger:synthetic",
        tq_result_path=path,
    )
    assert tampered["tq_identity"] is None
    assert tampered["reason_code"] == "EVIDENCE_INVALID"
