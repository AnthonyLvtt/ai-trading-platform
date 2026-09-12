"""Pure Shadow/evidence evaluation. No transport is accepted by either entry point."""

import re

from atp.exchange.filters import NotionalPriceEvidence, SymbolFilterEvidence, check_market_filters
from atp.exchange.model import ExchangePolicy, UpstreamOrderProof, valid
from atp.exchange.read_only import EvidenceError, safe_json, verify_record
from atp.exchange.shadow import project_test_shadow
from atp.risk.model import RiskDecision, RiskStatus
from atp.shared.identity import ContentIdentity
from atp.strategy import StrategyEvaluation
from atp.testnet_qualification.model import (
    CASES,
    ExchangeContractEvidence,
    Reason,
    ShadowEvaluationResult,
    ShadowIntent,
    Status,
    TestnetCapabilityEvidence,
    TestnetQualificationPolicy,
    TestnetQualificationResult,
    TestnetQualificationSuiteResult,
)


def shadow(
    proof: object,
    risk: object,
    strategy: object,
    filters: object,
    price: object = None,
    *,
    target: object = "TESTNET",
    policy: object = None,
) -> ShadowEvaluationResult:
    def blocked(reason: Reason) -> ShadowEvaluationResult:
        return ShadowEvaluationResult(Status.BLOCKED, reason)

    if type(target) is str and target == "LIVE":
        return blocked(Reason.LIVE_FORBIDDEN)
    if type(target) is not str or target != "TESTNET":
        return blocked(Reason.TESTNET_ENVIRONMENT_MISMATCH)
    p = TestnetQualificationPolicy() if policy is None else policy
    if not verify_record(p, TestnetQualificationPolicy):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
    if (
        not valid(risk, RiskDecision)
        or not isinstance(risk, RiskDecision)
        or risk.status is not RiskStatus.APPROVED
    ):
        return blocked(Reason.RISK_APPROVAL_REQUIRED)
    if risk.provenance.environment != "TEST":
        return blocked(Reason.TESTNET_ENVIRONMENT_MISMATCH)
    if proof is None:
        return blocked(Reason.SIZING_EVIDENCE_REQUIRED)
    if not valid(proof, UpstreamOrderProof) or not valid(strategy, StrategyEvaluation):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
    assert isinstance(proof, UpstreamOrderProof) and isinstance(strategy, StrategyEvaluation)
    if proof.quantity <= 0:
        return blocked(Reason.SIZING_EVIDENCE_REQUIRED)
    try:
        projection = project_test_shadow(proof, risk, strategy)
        if projection is None:
            return blocked(Reason.RISK_APPROVAL_REQUIRED)
        error = check_market_filters(filters, proof.symbol, proof.quantity, proof.created_at, price)
        if error:
            return blocked(Reason(error))
        assert isinstance(filters, SymbolFilterEvidence) and isinstance(
            p, TestnetQualificationPolicy
        )
        if price is not None and not verify_record(price, NotionalPriceEvidence):
            return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
        return ShadowEvaluationResult(
            Status.PASSED,
            Reason.TESTNET_QUALIFICATION_PASSED,
            ShadowIntent(
                proof.symbol,
                proof.side.value,
                strategy.content_identity,
                risk.content_identity,
                proof.content_identity,
                projection,
                filters.content_identity,
                price.content_identity if isinstance(price, NotionalPriceEvidence) else None,
                p.content_identity,
            ),
        )
    except (EvidenceError, ValueError, TypeError, AttributeError):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)


def qualify(
    evidence: object, *, policy: object = None, target: object = "TESTNET"
) -> TestnetQualificationSuiteResult:
    p = TestnetQualificationPolicy()
    adapter = ExchangePolicy().content_identity
    required = tuple(c.case_id for c in CASES)

    def blocked(reason: Reason) -> TestnetQualificationSuiteResult:
        return TestnetQualificationSuiteResult(
            None, None, adapter, p.content_identity, required, (), Status.BLOCKED, reason
        )

    if type(target) is str and target == "LIVE":
        return blocked(Reason.LIVE_FORBIDDEN)
    if (
        type(target) is not str
        or target != "TESTNET"
        or (policy is not None and not verify_record(policy, TestnetQualificationPolicy))
    ):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
    if type(evidence) in (dict, list):
        try:
            safe_json(evidence)
        except EvidenceError:
            return blocked(Reason.CREDENTIAL_MATERIAL_DETECTED)
    if not verify_record(evidence, TestnetCapabilityEvidence):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
    assert isinstance(evidence, TestnetCapabilityEvidence)
    if not re.fullmatch(r"[0-9a-f]{40}", evidence.source_commit_sha):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
    ids = [e.case_id for e in evidence.contracts]
    if len(ids) != len(set(ids)) or set(ids) != set(required):
        return blocked(Reason.INVALID_TESTNET_QUALIFICATION_INPUT)
    results = []
    for case in CASES:
        e = next(e for e in evidence.contracts if e.case_id == case.case_id)
        status, reason = _case(e, case.probes, evidence, adapter, p.content_identity)
        if status is Status.FAILED and reason is not Reason.SIDE_EFFECT_DETECTED:
            reason = case.failure_reason
        results.append(TestnetQualificationResult(case.case_id, status, reason, e.content_identity))
    status = (
        Status.BLOCKED
        if any(r.status is Status.BLOCKED for r in results)
        else Status.FAILED
        if any(r.status is Status.FAILED for r in results)
        else Status.PASSED
    )
    reason = next(
        (r.reason_code for r in results if r.status is status), Reason.TESTNET_QUALIFICATION_PASSED
    )
    return TestnetQualificationSuiteResult(
        evidence.source_commit_sha,
        evidence.repository_identity,
        adapter,
        p.content_identity,
        required,
        tuple(results),
        status,
        reason,
    )


def _case(
    e: ExchangeContractEvidence,
    probes: tuple[str, ...],
    source: TestnetCapabilityEvidence,
    adapter: ContentIdentity,
    policy: ContentIdentity,
) -> tuple[Status, Reason]:
    if e.side_effects_performed:
        return Status.FAILED, Reason.SIDE_EFFECT_DETECTED
    if (
        e.source_commit_sha != source.source_commit_sha
        or e.repository_identity != source.repository_identity
        or e.policy_identity != policy
        or e.adapter_identity != adapter
        or e.level != "OFFLINE_CONTRACT"
    ):
        return Status.BLOCKED, Reason.INVALID_TESTNET_QUALIFICATION_INPUT
    observations = e.observations
    if (
        any(not o.subject_identities for o in observations)
        or set(o.probe_id for o in observations) != set(probes)
        or len({o.node_id for o in observations}) != len(observations)
        or any(o.node_id.split("::")[-1].split("[")[0] != o.probe_id for o in observations)
        or any(
            o.setup != "passed" or o.teardown != "passed" or o.call not in ("passed", "failed")
            for o in observations
        )
    ):
        return (
            Status.BLOCKED,
            Reason.RECONCILIATION_CAPABILITY_MISSING
            if e.case_id == "TQ-RECONCILE-001"
            else Reason.ADAPTER_CONTRACT_INVALID,
        )
    return (
        (Status.FAILED, Reason.ADAPTER_CONTRACT_INVALID)
        if any(o.call == "failed" for o in observations)
        else (Status.PASSED, Reason.TESTNET_QUALIFICATION_PASSED)
    )


def inspect_qualification(
    result: object, evidence: object, *, source_commit_sha: object, repository_identity: object
) -> bool:
    """Consumer check against its own exact source, not a caller's PASSED label."""
    if (
        not verify_record(result, TestnetQualificationSuiteResult)
        or not verify_record(evidence, TestnetCapabilityEvidence)
        or type(source_commit_sha) is not str
        or not verify_record(repository_identity, ContentIdentity)
    ):
        return False
    assert isinstance(result, TestnetQualificationSuiteResult)
    assert isinstance(evidence, TestnetCapabilityEvidence)
    return (
        result.source_commit_sha == evidence.source_commit_sha == source_commit_sha
        and result.repository_identity == evidence.repository_identity == repository_identity
        and result == qualify(evidence)
    )
