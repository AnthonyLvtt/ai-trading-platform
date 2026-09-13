import copy
import socket
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from atp.exchange.model import Reason as ExchangeReason
from atp.exchange.submission_gate import authorize_exchange_submission
from atp.exchange.transport import BinanceTestnetHTTPTransport, Operation
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.composition import validate_activation
from atp.testnet_activation.contracts import (
    ActivationPolicy,
    Reason,
    context_error,
)
from tests.activation_support import (
    SyntheticCredentialCapabilityAuthority,
    SyntheticTrustedActivationAuthority,
    activation,
    complete_chain,
)
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW

# Explicit fixture exports for pytest.
__all__ = ["inputs", "activation"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"grant": None}, Reason.ACTIVATION_GRANT_REQUIRED),
        ({"grant": "CTO"}, Reason.ACTIVATION_GRANT_INVALID),
        ({"grant_authority": None}, Reason.ACTIVATION_GRANT_UNTRUSTED),
        ({"source_commit_sha": "b" * 40}, Reason.ACTIVATION_SOURCE_MISMATCH),
        (
            {"repository_identity": ContentIdentity.from_text("other")},
            Reason.ACTIVATION_SOURCE_MISMATCH,
        ),
        ({"release": None}, Reason.RELEASE_BINDING_REQUIRED),
        ({"wheel": b"bad"}, Reason.RELEASE_BINDING_MISMATCH),
        ({"tq": None}, Reason.TESTNET_QUALIFICATION_REQUIRED),
        ({"tq_evidence": None}, Reason.TESTNET_QUALIFICATION_INVALID),
        ({"symbol": "ETHUSDT"}, Reason.SYMBOL_NOT_AUTHORIZED),
        ({"order_type": "LIMIT"}, Reason.ORDER_TYPE_NOT_AUTHORIZED),
        ({"credential_source_identity": None}, Reason.CREDENTIAL_CAPABILITY_REQUIRED),
        ({"credential_authority": None}, Reason.CREDENTIAL_CAPABILITY_INVALID),
        ({"at": NOW + timedelta(days=1)}, Reason.ACTIVATION_GRANT_EXPIRED),
        ({"at": NOW - timedelta(days=1)}, Reason.ACTIVATION_GRANT_NOT_YET_VALID),
        ({"at": "now"}, Reason.INVALID_ACTIVATION_INPUT),
        ({"environment": "LIVE"}, Reason.LIVE_FORBIDDEN),
        ({"environment": []}, Reason.INVALID_ACTIVATION_INPUT),
    ],
)
def test_activation_fail_closed(activation, change, reason):
    assert validate_activation(**(activation | change)).reason_code is reason


@pytest.mark.parametrize(
    ("present", "trading", "withdrawal", "reason"),
    [
        (False, True, True, Reason.CREDENTIAL_CAPABILITY_INVALID),
        (True, None, True, Reason.CREDENTIAL_CAPABILITY_INVALID),
        (True, True, None, Reason.CREDENTIAL_CAPABILITY_INVALID),
        (True, True, False, Reason.WITHDRAWAL_CAPABILITY_FORBIDDEN),
    ],
)
def test_trusted_capabilities_are_required(activation, present, trading, withdrawal, reason):
    activation["credential_authority"] = SyntheticCredentialCapabilityAuthority(
        present, trading, withdrawal
    )
    assert validate_activation(**activation).reason_code is reason


def test_caller_booleans_are_not_attestation(activation):
    activation["credential_authority"] = activation["credential_authority"].attest(
        activation["credential_source_identity"]
    )
    assert validate_activation(**activation).reason_code is Reason.CREDENTIAL_CAPABILITY_INVALID


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_active_positions", 2),
        ("allowed_symbols", ("*",)),
        ("allowed_symbols", ("ALL",)),
        ("allowed_symbols", ("ANY",)),
        ("allowed_symbols", ()),
        ("validity_end", NOW - timedelta(days=1)),
    ],
)
def test_invalid_grant_scope(activation, field, value):
    with pytest.raises(ValueError):
        replace(activation["grant"], **{field: value})


def test_different_pin_and_fake_role_do_not_authorize(activation):
    assert activation["grant"].issued_by_role == "CTO"
    assert (
        validate_activation(**(activation | {"grant_authority": None})).reason_code
        is Reason.ACTIVATION_GRANT_UNTRUSTED
    )
    activation["grant_authority"].pin = ContentIdentity.from_text("different")
    assert validate_activation(**activation).reason_code is Reason.ACTIVATION_GRANT_UNTRUSTED


def test_identity_determinism_and_context_authenticity(activation):
    a = validate_activation(**activation)
    b = validate_activation(**activation)
    assert a == b
    assert context_error(a.context, NOW) is None
    copied = replace(a.context)
    assert context_error(copied, NOW) is Reason.ACTIVATION_GRANT_UNTRUSTED
    mutated = copy.copy(a.context)
    object.__setattr__(mutated, "symbol", "ETHUSDT")
    assert context_error(mutated, NOW) is Reason.INVALID_ACTIVATION_INPUT
    grant = replace(activation["grant"], validity_end=NOW + timedelta(hours=2))
    changed = validate_activation(
        **(
            activation
            | {"grant": grant, "grant_authority": SyntheticTrustedActivationAuthority(grant)}
        )
    )
    assert changed.context.content_identity != a.context.content_identity


def test_expiry_rechecked_by_all_consumers(activation, tmp_path):
    order, args = complete_chain(activation, tmp_path)
    from atp.ops import readiness
    from atp.release_deployment import promote
    from atp.release_deployment.model import Target
    from tests.unit.test_ops import config

    future = activation["grant"].validity_end
    assert context_error(args["runtime_authorization"], future) is Reason.ACTIVATION_GRANT_EXPIRED
    assert (
        promote(
            args["release"],
            args["wheel"],
            Target.TESTNET,
            runtime_authorization=args["runtime_authorization"],
            at=future,
        ).reason_code.value
        == "ACTIVATION_GRANT_EXPIRED"
    )
    assert (
        readiness(
            config(tmp_path, "TESTNET"),
            runtime_authorization=args["runtime_authorization"],
            at=future,
        ).reason_code.value
        == "ACTIVATION_GRANT_EXPIRED"
    )
    assert (
        authorize_exchange_submission(order, **(args | {"at": future})).reason_code
        is Reason.ACTIVATION_GRANT_EXPIRED
    )


def test_mutated_policy_and_runtime_objects(activation):
    p = ActivationPolicy()
    object.__setattr__(p, "first_testnet_order_authorized", True)
    assert (
        validate_activation(**activation, policy=p).reason_code is Reason.INVALID_ACTIVATION_INPUT
    )
    for bad in (42, [], object(), {"issued_by_role": "CTO"}):
        assert validate_activation(**(activation | {"grant": bad})).status == "BLOCKED"
        assert context_error(bad, NOW) is Reason.INVALID_ACTIVATION_INPUT


def test_http_transport_cannot_submit_even_with_ready_ops(activation, tmp_path):
    order, args = complete_chain(activation, tmp_path)
    credentials = Mock()
    transport = BinanceTestnetHTTPTransport(credentials)
    reply = transport.perform(Operation.SUBMIT, order, NOW, args["readiness"])
    assert reply.error is ExchangeReason.TESTNET_RUNTIME_BLOCKED
    credentials.assert_not_called()


def test_reconciliation_missing_and_tampered_tq(activation):
    from atp.testnet_qualification import qualify

    evidence = activation["tq_evidence"]
    broken = replace(
        evidence,
        contracts=tuple(
            replace(c, observations=()) if c.case_id == "TQ-RECONCILE-001" else c
            for c in evidence.contracts
        ),
    )
    assert (
        validate_activation(
            **(activation | {"tq": qualify(broken), "tq_evidence": broken})
        ).reason_code
        is Reason.RECONCILIATION_NOT_READY
    )
    forged = copy.copy(activation["tq"])
    object.__setattr__(forged, "content_identity", ContentIdentity.from_text("fake"))
    assert (
        validate_activation(**(activation | {"tq": forged})).reason_code
        is Reason.TESTNET_QUALIFICATION_INVALID
    )


@pytest.mark.parametrize(
    "field,reason",
    [
        ("testnet_qualification_identity", Reason.TESTNET_QUALIFICATION_MISMATCH),
        ("release_candidate_identity", Reason.RELEASE_BINDING_MISMATCH),
        ("release_manifest_identity", Reason.RELEASE_BINDING_MISMATCH),
    ],
)
def test_trusted_but_mismatched_grant_evidence(activation, field, reason):
    grant = replace(activation["grant"], **{field: ContentIdentity.from_text("other")})
    assert (
        validate_activation(
            **(
                activation
                | {"grant": grant, "grant_authority": SyntheticTrustedActivationAuthority(grant)}
            )
        ).reason_code
        is reason
    )


def test_risk_retains_economic_constraints_and_context_binding(activation, tmp_path):
    from atp.risk import DeterministicRiskEngine, RiskEvaluationContext, RiskStatus
    from tests.unit.test_risk_engine import (
        empty_portfolio,
        market_context,
        open_portfolio,
        open_position,
        policy,
    )

    order, args = complete_chain(activation, tmp_path)
    engine = DeterministicRiskEngine(policy())
    base = RiskEvaluationContext(
        args["strategy"], market_context(environment="TESTNET"), empty_portfolio()
    )
    assert engine.evaluate(base).status is RiskStatus.BLOCKED
    authorized = replace(base, runtime_authorization=args["runtime_authorization"])
    assert engine.evaluate(authorized).status is RiskStatus.APPROVED
    assert engine.evaluate(replace(authorized, portfolio_state=None)).status is RiskStatus.BLOCKED
    assert (
        engine.evaluate(replace(authorized, portfolio_state=open_portfolio(open_position()))).status
        is RiskStatus.REJECTED
    )
    assert (
        engine.evaluate(
            replace(authorized, market_context=market_context(environment="LIVE"))
        ).status
        is RiskStatus.BLOCKED
    )
    assert (
        engine.evaluate(
            replace(authorized, runtime_authorization=replace(args["runtime_authorization"]))
        ).status
        is RiskStatus.BLOCKED
    )


def test_changed_grant_changes_order_id_and_rejects_old_gates(activation, tmp_path):
    order, args = complete_chain(activation, tmp_path)
    grant = replace(activation["grant"], validity_end=NOW + timedelta(hours=2))
    other = activation | {
        "grant": grant,
        "grant_authority": SyntheticTrustedActivationAuthority(grant),
    }
    second, args2 = complete_chain(other, tmp_path)
    assert second.client_order_id != order.client_order_id
    assert (
        authorize_exchange_submission(
            second, **(args2 | {"readiness": args["readiness"]})
        ).reason_code
        is Reason.OPS_NOT_READY
    )
    assert (
        authorize_exchange_submission(
            second, **(args2 | {"promotion": args["promotion"]})
        ).reason_code
        is Reason.RELEASE_NOT_PROMOTED
    )
    assert authorize_exchange_submission(second, **(args2 | {"filters": None})).status == "BLOCKED"


def test_no_secret_echo_in_composition_results(activation, caplog, capsys):
    class ContaminatedAuthority(SyntheticCredentialCapabilityAuthority):
        def attest(self, source):
            return replace(
                super().attest(source), authority_reference="api_secret=DO_NOT_ECHO_THIS"
            )

    result = validate_activation(**(activation | {"credential_authority": ContaminatedAuthority()}))
    assert result.reason_code is Reason.CREDENTIAL_CAPABILITY_INVALID
    out = capsys.readouterr()
    assert "DO_NOT_ECHO_THIS" not in repr(result) + caplog.text + out.out + out.err


def test_conditional_deployment_remains_local_without_start(activation, tmp_path):
    from atp.release_deployment import deploy, plan_deployment
    from atp.release_deployment.model import DeploymentStatus, Target

    context = validate_activation(**activation).context
    plan = plan_deployment(
        activation["release"],
        activation["wheel"],
        Target.TESTNET,
        str(tmp_path / "testnet-copy"),
        runtime_authorization=context,
        at=NOW,
    )
    assert plan.plan is not None
    result = deploy(
        activation["release"], activation["wheel"], plan.plan, runtime_authorization=context, at=NOW
    )
    assert result.status is DeploymentStatus.COMPLETED and result.process_started is False
    assert (
        deploy(activation["release"], activation["wheel"], plan.plan).status
        is DeploymentStatus.BLOCKED
    )


def test_public_activation_paths_have_no_network_or_runtime_authority():
    import ast
    from pathlib import Path

    paths = list(Path("src/atp/testnet_activation").glob("*.py")) + [
        Path("src/atp/exchange/submission_gate.py")
    ]
    forbidden = ("requests", "httpx", "socket", "subprocess", "atp.oms", "atp.ai", "atp.ml")
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in (
                    "submit",
                    "perform",
                    "_dispatch",
                    "cancel",
                    "withdraw",
                    "getenv",
                )
    from atp.testnet_activation.composition import (
        CredentialCapabilityAuthority,
        TrustedActivationAuthority,
    )

    with pytest.raises(TypeError):
        TrustedActivationAuthority()
    with pytest.raises(TypeError):
        CredentialCapabilityAuthority()


def test_hostile_external_types_never_stringify(activation):
    class Hostile:
        def __str__(self):
            raise AssertionError("must not stringify")

        def __repr__(self):
            raise AssertionError("must not repr")

        def __eq__(self, other):
            raise AssertionError("must not compare")

    for name in (
        "environment",
        "source_commit_sha",
        "repository_identity",
        "symbol",
        "order_type",
        "at",
        "grant",
    ):
        assert validate_activation(**(activation | {name: Hostile()})).status == "BLOCKED"


def test_web_exposes_only_safe_ready_projection(activation, tmp_path):
    from atp.web import WebState, project, reference
    from atp.web.model import ArtifactReference, ReadinessView

    _, args = complete_chain(activation, tmp_path)
    ref = reference(args["readiness"])
    assert isinstance(ref, ArtifactReference)
    view = project(WebState(readiness_result=ref), "/readiness")
    assert isinstance(view, ReadinessView)
    rendered = view.model_dump_json()
    assert "synthetic-capability-authority" not in rendered
    assert "trading_allowed" not in rendered and "api_secret" not in rendered
