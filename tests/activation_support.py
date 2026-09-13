"""Synthetic authorities exist only in tests; no runtime selector is provided."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atp.exchange.model import Side, UpstreamOrderProof, request_from_proof
from atp.ops import observability_evidence, readiness
from atp.ops.model import QualificationReference
from atp.release_deployment import promote
from atp.release_deployment.model import Target
from atp.risk import DeterministicRiskEngine, RiskEvaluationContext, RiskStatus
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from atp.strategy import SignalKind, StrategyEvaluation
from atp.testnet_activation.composition import (
    CredentialCapabilityAuthority,
    TrustedActivationAuthority,
    validate_activation,
)
from atp.testnet_activation.contracts import (
    ActivationPolicy,
    TestnetActivationGrant,
    TrustedActivationGrantEvidence,
    TrustedCredentialCapabilityEvidence,
)
from atp.testnet_qualification import qualify
from tests.exchange_support import record
from tests.unit.test_ops import config
from tests.unit.test_release_deployment import qualified
from tests.unit.test_risk_engine import (
    NOW,
    empty_portfolio,
    market_context,
    policy,
    strategy_evaluation,
)
from tests.unit.test_testnet_qualification import evidence_fixture, filters


class SyntheticTrustedActivationAuthority(TrustedActivationAuthority):
    def __init__(self, grant):
        self.pin = grant.content_identity

    @property
    def accepted_grant_identity(self):
        return self.pin

    def attest(self, grant):
        return TrustedActivationGrantEvidence(
            grant.content_identity,
            ActivationPolicy().content_identity,
            grant.source_commit_sha,
            grant.repository_identity,
            grant.release_candidate_identity,
            grant.testnet_qualification_identity,
            "synthetic-test-authority",
        )


class SyntheticCredentialCapabilityAuthority(CredentialCapabilityAuthority):
    def __init__(self, present=True, trading=True, withdrawal_absent=True):
        self.present, self.trading, self.withdrawal_absent = present, trading, withdrawal_absent

    def attest(self, source):
        return TrustedCredentialCapabilityEvidence(
            self.present,
            self.trading,
            self.withdrawal_absent,
            source,
            "synthetic-capability-authority",
        )


@pytest.fixture
def activation(inputs):
    release = qualified(inputs)
    source = release.source
    raw = evidence_fixture()
    evidence = replace(
        raw,
        source_commit_sha=source.source_commit_sha,
        repository_identity=source.repository_identity,
        contracts=tuple(
            replace(
                c,
                source_commit_sha=source.source_commit_sha,
                repository_identity=source.repository_identity,
            )
            for c in raw.contracts
        ),
    )
    tq = qualify(evidence)
    grant = TestnetActivationGrant(
        source.source_commit_sha,
        source.repository_identity,
        release.candidate.content_identity,
        release.manifest.content_identity,
        tq.content_identity,
        ("BTCUSDT",),
        ("MARKET",),
        NOW - timedelta(hours=1),
        NOW + timedelta(hours=1),
        "CTO",
    )
    return dict(
        grant=grant,
        source_commit_sha=source.source_commit_sha,
        repository_identity=source.repository_identity,
        release=release,
        wheel=inputs[4],
        tq=tq,
        tq_evidence=evidence,
        credential_source_identity=ContentIdentity.from_text("opaque-test-provider"),
        symbol="BTCUSDT",
        order_type="MARKET",
        at=NOW,
        grant_authority=SyntheticTrustedActivationAuthority(grant),
        credential_authority=SyntheticCredentialCapabilityAuthority(),
    )


def complete_chain(activation, tmp_path):
    context = validate_activation(**activation).context
    assert context is not None
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    q = activation["release"].qualification.result
    ops = readiness(
        config(tmp_path, "TESTNET") | {"qualification_required": True},
        qualification=QualificationReference(q, q.content_identity, q.qualification_run_id),
        observability=observability_evidence(),
        runtime_authorization=context,
        at=NOW,
    )
    promotion = promote(
        activation["release"],
        activation["wheel"],
        Target.TESTNET,
        runtime_authorization=context,
        at=NOW,
    )
    original = strategy_evaluation(SignalKind.LONG_ENTRY)
    strategy = StrategyEvaluation.completed(
        replace(original.provenance, environment=Environment.TESTNET), SignalKind.LONG_ENTRY
    )
    risk = DeterministicRiskEngine(policy()).evaluate(
        RiskEvaluationContext(
            strategy, market_context(environment="TESTNET"), empty_portfolio(), context
        )
    )
    assert risk.status is RiskStatus.APPROVED, risk.reason_code
    proof = record(
        UpstreamOrderProof,
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=Decimal("0.001"),
        risk_decision_id=str(risk.decision.risk_decision_id),
        risk_decision_identity=risk.decision.content_identity,
        strategy_evaluation_identity=strategy.content_identity,
        environment="TESTNET",
        created_at=NOW,
    )
    return request_from_proof(proof), dict(
        proof=proof,
        risk=risk.decision,
        strategy=strategy,
        grant=activation["grant"],
        runtime_authorization=context,
        readiness=ops,
        release=activation["release"],
        wheel=activation["wheel"],
        promotion=promotion,
        filters=filters(),
        at=NOW,
    )
