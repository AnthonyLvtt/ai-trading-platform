import ast
import copy
import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from atp.exchange.adapter import ExchangeAdapter
from atp.exchange.filters import (
    NotionalPriceEvidence,
    SymbolFilterEvidence,
    check_market_filters,
    parse_symbol_filters,
)
from atp.exchange.model import (
    ExchangePolicy,
    UpstreamOrderProof,
)
from atp.exchange.model import (
    Reason as ExchangeReason,
)
from atp.exchange.model import (
    Status as ExchangeStatus,
)
from atp.exchange.read_only import ExchangeOrderStatus, ReconciliationLookup, parse_reconciliation
from atp.exchange.shadow import inspect_exchange_reply
from atp.exchange.transport import BinanceTestnetHTTPTransport, TransportReply
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.testnet_qualification import qualify, shadow
from atp.testnet_qualification.model import (
    CASES,
    ExchangeContractEvidence,
    ProbeObservation,
    Reason,
    Status,
    TestnetCapabilityEvidence,
    TestnetQualificationPolicy,
)
from tests.exchange_support import inputs, record
from tests.unit.test_risk_engine import NOW


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TQ must not reach transport/submission")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(ExchangeAdapter, "submit", forbidden)
    monkeypatch.setattr(ExchangeAdapter, "reconcile", forbidden)
    monkeypatch.setattr(BinanceTestnetHTTPTransport, "perform", forbidden)


@pytest.fixture
def scenario(request):
    order, context = inputs()
    request.node.user_properties.append(("tq_subject", str(order.content_identity)))
    request.node.user_properties.append(("tq_subject", str(context["risk"].content_identity)))
    return order, context


def symbol_payload():
    return {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "orderTypes": ["MARKET"],
        "filters": [
            {
                "filterType": "PRICE_FILTER",
                "minPrice": "1",
                "maxPrice": "1000000",
                "tickSize": "0.01",
            },
            {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "2", "stepSize": "0.001"},
        ],
    }


def filters(payload=None):
    payload = symbol_payload() if payload is None else payload
    return SymbolFilterEvidence(canonical_json_bytes(payload), NOW)


def run_shadow(scenario, **kwargs):
    _, c = scenario
    return shadow(c["proof"], c["risk"], c["strategy"], kwargs.pop("filters", filters()), **kwargs)


def test_tq_adapter_binding(scenario):
    result = run_shadow(scenario)
    assert result.status is Status.PASSED
    assert result.intent.source_risk_environment == "TEST"
    assert result.intent.qualification_target == "TESTNET"
    assert result.intent.submission_authorized is False
    assert result.intent.projection.adapter_policy_identity == ExchangePolicy().content_identity
    assert not result.side_effect_performed
    assert run_shadow(scenario, target="LIVE").reason_code is Reason.LIVE_FORBIDDEN
    assert run_shadow(scenario, target="LOCAL").status is Status.BLOCKED


def test_tq_serialization(scenario):
    projected = run_shadow(scenario).intent.projection
    assert dict(projected.parameters()) == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": "0.001",
        "newClientOrderId": scenario[0].client_order_id,
    }
    assert run_shadow(scenario) == run_shadow(scenario)
    assert "timestamp" not in dict(projected.parameters())
    assert "signature" not in repr(projected)


def test_tq_idempotency(scenario):
    order, c = scenario
    first = run_shadow(scenario).intent
    data = {
        name: getattr(c["proof"], name)
        for name in (
            "symbol",
            "side",
            "risk_decision_id",
            "risk_decision_identity",
            "strategy_evaluation_identity",
            "environment",
            "created_at",
        )
    }
    changed = record(UpstreamOrderProof, **data, quantity=Decimal("0.002"))
    second = shadow(changed, c["risk"], c["strategy"], filters()).intent
    assert first.client_intent_id != second.client_intent_id
    assert first.projection.client_order_id != second.projection.client_order_id
    assert first.projection.client_order_id == order.client_order_id
    assert run_shadow(scenario).intent == first  # duplicate pure projection, never a POST


@pytest.mark.parametrize(
    "code,http,expected",
    [
        (-1007, 400, ExchangeStatus.UNCERTAIN),
        (-1006, 400, ExchangeStatus.UNCERTAIN),
        (-2015, 401, ExchangeStatus.REJECTED),
        (-2014, 403, ExchangeStatus.REJECTED),
        (-1003, 429, ExchangeStatus.REJECTED),
        (-2010, 400, ExchangeStatus.REJECTED),
        (-1021, 400, ExchangeStatus.REJECTED),
        (-1, 500, ExchangeStatus.UNCERTAIN),
    ],
)
def test_tq_error_mapping(scenario, code, http, expected):
    order, _ = scenario
    result = inspect_exchange_reply(
        TransportReply(http, {"code": code, "msg": "fixture error"}), order, NOW
    )
    assert result.status is expected
    assert "fixture error" not in repr(result)


def test_tq_unknown_no_retry(scenario):
    order, _ = scenario
    timeout = inspect_exchange_reply(
        TransportReply(error=ExchangeReason.TRANSPORT_TIMEOUT, possibly_sent=True), order, NOW
    )
    assert timeout.status is ExchangeStatus.UNCERTAIN
    assert timeout.reason_code is ExchangeReason.SUBMISSION_OUTCOME_UNKNOWN
    before = inspect_exchange_reply(
        TransportReply(error=ExchangeReason.TRANSPORT_UNAVAILABLE), order, NOW
    )
    assert before.status is ExchangeStatus.BLOCKED
    unknown = parse_reconciliation(
        ReconciliationLookup("BTCUSDT", exchange_order_id=1), order_payload("ALIEN"), []
    )
    assert unknown.snapshot.status is ExchangeOrderStatus.UNKNOWN
    assert unknown.status == "BLOCKED" and unknown.safe_to_retry is False


@pytest.mark.parametrize(
    "mutation,quantity,expected",
    [
        ("none", "0.001", None),
        ("none", "0.0001", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("none", "3", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("none", "0.0015", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("missing", "0.001", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("unknown", "0.001", "UNSUPPORTED_SYMBOL_FILTER"),
        ("duplicate", "0.001", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("halt", "0.001", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("limit_only", "0.001", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("market_priority", "0.0015", None),
        ("bad_decimal", "0.001", "SYMBOL_FILTER_INCOMPATIBLE"),
        ("future", "0.001", "SYMBOL_FILTER_INCOMPATIBLE"),
    ],
)
def test_tq_filters(mutation, quantity, expected, record_property):
    payload = symbol_payload()
    if mutation == "missing":
        payload["filters"] = []
    if mutation == "unknown":
        payload["filters"].append({"filterType": "FUTURE_FILTER"})
    if mutation == "duplicate":
        payload["filters"].append(copy.deepcopy(payload["filters"][-1]))
    if mutation == "halt":
        payload["status"] = "BREAK"
    if mutation == "limit_only":
        payload["orderTypes"] = ["LIMIT"]
    if mutation == "bad_decimal":
        payload["filters"][-1]["stepSize"] = True
    if mutation == "market_priority":
        payload["filters"].append(
            {
                "filterType": "MARKET_LOT_SIZE",
                "minQty": "0.0001",
                "maxQty": "3",
                "stepSize": "0.0001",
            }
        )
    evidence = filters(payload)
    if mutation == "future":
        evidence = replace(evidence, observed_at=NOW + timedelta(seconds=1))
    record_property("tq_subject", str(evidence.content_identity))
    with localcontext() as context:
        context.prec = 2  # enforcement must not round quantities in the ambient Decimal context
        assert check_market_filters(evidence, "BTCUSDT", Decimal(quantity), NOW) == expected
    if mutation == "none":
        assert parse_symbol_filters(payload, NOW) == evidence


@pytest.mark.parametrize(
    "source,window,at_delta,ok",
    [
        ("EXCHANGE_AVERAGE_PRICE", 5, 0, True),
        ("EXCHANGE_LAST_PRICE", 5, 0, False),
        ("DATA_MARK", 5, 0, False),
        ("EXCHANGE_AVERAGE_PRICE", 4, 0, False),
        ("EXCHANGE_AVERAGE_PRICE", 5, 1, False),
        ("MISSING", 5, 0, False),
    ],
)
def test_tq_price_evidence(source, window, at_delta, ok, record_property):
    payload = symbol_payload()
    payload["filters"].append(
        {
            "filterType": "NOTIONAL",
            "minNotional": "10",
            "maxNotional": "100",
            "applyMinToMarket": True,
            "applyMaxToMarket": True,
            "avgPriceMins": 5,
        }
    )
    # NOTIONAL takes priority, so the incompatible lower-priority filter cannot decide.
    payload["filters"].append(
        {
            "filterType": "MIN_NOTIONAL",
            "minNotional": "9999",
            "applyToMarket": True,
            "avgPriceMins": 0,
        }
    )
    evidence = filters(payload)
    price = (
        None
        if source == "MISSING"
        else NotionalPriceEvidence(
            "BTCUSDT",
            Decimal("20000"),
            source,
            ContentIdentity.from_text("exchange-price-fixture"),
            NOW + timedelta(seconds=at_delta),
            NOW,
            window,
        )
    )
    record_property("tq_subject", str(evidence.content_identity))
    assert (check_market_filters(evidence, "BTCUSDT", Decimal("0.001"), NOW, price) is None) is ok
    payload["filters"][-2]["applyMinToMarket"] = False
    payload["filters"][-2]["applyMaxToMarket"] = False
    assert check_market_filters(filters(payload), "BTCUSDT", Decimal("0.001"), NOW) is None


def order_payload(status="FILLED"):
    return {
        "symbol": "BTCUSDT",
        "orderId": 1,
        "clientOrderId": "atp-fixture",
        "status": status,
        "side": "BUY",
        "type": "MARKET",
        "origQty": "0.001",
        "executedQty": "0.001",
        "updateTime": 1000,
    }


@pytest.mark.parametrize("status", [s.value for s in ExchangeOrderStatus])
def test_tq_reconciliation(status, record_property):
    payload = order_payload(status)
    trades = [
        {
            "id": 12,
            "orderId": 1,
            "symbol": "BTCUSDT",
            "price": "12345.1234567890123456789",
            "qty": "0.001",
            "quoteQty": "12.3451234567890123456789",
            "time": 999,
        }
    ]
    first = ReconciliationLookup("BTCUSDT", client_order_id="atp-fixture")
    second = ReconciliationLookup("BTCUSDT", exchange_order_id=1)
    assert dict(first.parameters())["origClientOrderId"] == "atp-fixture"
    assert dict(second.parameters())["orderId"] == "1"
    result = parse_reconciliation(first, payload, trades)
    assert result.snapshot == parse_reconciliation(second, payload, trades).snapshot
    assert result.snapshot.status.value == status
    assert result.snapshot.fills[0].price == Decimal(trades[0]["price"])
    assert result.side_effect_performed is False and result.safe_to_retry is False
    record_property("tq_subject", str(result.content_identity))
    assert parse_reconciliation(first, payload, trades * 2).status == "BLOCKED"
    assert parse_reconciliation(second, payload | {"orderId": 2}, trades).status == "BLOCKED"
    assert parse_reconciliation(second, payload, [{}]).status == "BLOCKED"


@pytest.mark.parametrize("bad", [None, True, 42, [], {}, object()])
def test_tq_malformed(scenario, bad):
    order, c = scenario
    assert shadow(bad, c["risk"], c["strategy"], filters()).status is Status.BLOCKED
    assert shadow(c["proof"], bad, c["strategy"], filters()).status is Status.BLOCKED
    assert inspect_exchange_reply(bad, order, NOW).status is ExchangeStatus.UNCERTAIN
    assert parse_reconciliation(bad, {}, []).status == "BLOCKED"
    assert check_market_filters(bad, "BTCUSDT", Decimal("0.001"), NOW) is not None
    assert qualify(bad).status is Status.BLOCKED


def test_tq_risk_required(scenario):
    _, c = scenario
    assert (
        shadow(None, c["risk"], c["strategy"], filters()).reason_code
        is Reason.SIZING_EVIDENCE_REQUIRED
    )
    object.__setattr__(c["risk"], "status", "APPROVED")
    result = shadow(c["proof"], c["risk"], c["strategy"], filters())
    assert result.reason_code is Reason.RISK_APPROVAL_REQUIRED and result.intent is None


def test_tq_security(scenario, capsys):
    secret = "obviously-fixture-sensitive"
    assert qualify({"api_key": secret}).reason_code is Reason.CREDENTIAL_MATERIAL_DETECTED
    assert secret not in repr(qualify({"api_key": secret}))
    contaminated = symbol_payload() | {"token": secret}
    assert parse_symbol_filters(contaminated, NOW) is None
    assert run_shadow(scenario, target="LIVE").reason_code is Reason.LIVE_FORBIDDEN
    assert secret not in capsys.readouterr().out
    assert not any(
        hasattr(ExchangeAdapter, name)
        for name in ("withdraw", "transfer", "enable_live", "enable_testnet")
    )


def test_tq_boundaries():
    for path in Path("src/atp/testnet_qualification").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in (
                    "submit",
                    "perform",
                    "reconcile",
                    "cancel",
                    "withdraw",
                    "send",
                    "connect",
                )
            if isinstance(node, ast.ImportFrom):
                assert not set((node.module or "").split(".")) & {
                    "oms",
                    "ops",
                    "release_deployment",
                    "accounting",
                    "ai",
                    "ml",
                }
    assert ExchangePolicy().live_allowed is False


def evidence_fixture():
    repo = ContentIdentity.from_text("explicit-fixture-tree")
    policy = TestnetQualificationPolicy()
    contracts = tuple(
        ExchangeContractEvidence(
            c.case_id,
            "a" * 40,
            repo,
            ExchangePolicy().content_identity,
            policy.content_identity,
            tuple(ProbeObservation(p, p, "passed", "passed", "passed", (repo,)) for p in c.probes),
        )
        for c in CASES
    )
    return TestnetCapabilityEvidence("a" * 40, repo, contracts)


def test_tq_suite_integrity_and_aggregation():
    evidence = evidence_fixture()
    result = qualify(evidence)
    assert result.status is Status.PASSED and len(result.cases) == 10
    assert qualify(replace(evidence, contracts=evidence.contracts[::-1])) == result
    assert qualify(replace(evidence, contracts=evidence.contracts[:-1])).status is Status.BLOCKED
    contract = evidence.contracts[0]
    failed = replace(
        contract,
        observations=(replace(contract.observations[0], call="failed"),)
        + contract.observations[1:],
    )
    assert (
        qualify(replace(evidence, contracts=(failed,) + evidence.contracts[1:])).status
        is Status.FAILED
    )
    blocked = replace(evidence.contracts[1], observations=())
    assert (
        qualify(replace(evidence, contracts=(failed, blocked) + evidence.contracts[2:])).status
        is Status.BLOCKED
    )
    object.__setattr__(contract, "level", "OPTIONAL_READ_ONLY_CONNECTIVITY")
    assert qualify(evidence).status is Status.BLOCKED


def test_tq_policy_mutation_and_tampered_filters(scenario):
    policy = TestnetQualificationPolicy()
    object.__setattr__(policy, "shadow_only", False)
    assert qualify(evidence_fixture(), policy=policy).status is Status.BLOCKED
    assert run_shadow(scenario, policy=policy).status is Status.BLOCKED
    evidence = filters()
    object.__setattr__(
        evidence, "payload", canonical_json_bytes(symbol_payload() | {"status": "BREAK"})
    )
    assert run_shadow(scenario, filters=evidence).status is Status.BLOCKED


def test_tq_runtime_matrices_unchanged(tmp_path, scenario):
    from atp.ops import observability_evidence, readiness
    from atp.release_deployment import promote
    from atp.release_deployment.model import Target
    from atp.risk import DeterministicRiskEngine, RiskEvaluationContext, RiskStatus
    from tests.unit.test_ops import config
    from tests.unit.test_risk_engine import empty_portfolio, market_context, policy

    (tmp_path / "artifacts").mkdir()
    ops = readiness(config(tmp_path, "TESTNET"), observability=observability_evidence())
    assert ops.reason_code.value == "TESTNET_NOT_AUTHORIZED"
    for environment in ("TESTNET", "LIVE"):
        risk = DeterministicRiskEngine(policy()).evaluate(
            RiskEvaluationContext(
                scenario[1]["strategy"], market_context(environment=environment), empty_portfolio()
            )
        )
        assert risk.status is RiskStatus.BLOCKED
        assert promote(None, None, Target(environment)).status.value == "BLOCKED"


def test_tq_valid_nonapproval_and_environment_mismatch(scenario):
    from atp.risk import DeterministicRiskEngine, RiskEvaluationContext
    from tests.unit.test_risk_engine import market_context, open_portfolio, open_position, policy

    c = scenario[1]
    rejected = DeterministicRiskEngine(policy()).evaluate(
        RiskEvaluationContext(
            c["strategy"], market_context(environment="TEST"), open_portfolio(open_position())
        )
    )
    assert rejected.status.value == "REJECTED"
    assert (
        shadow(c["proof"], rejected.decision, c["strategy"], filters()).reason_code
        is Reason.RISK_APPROVAL_REQUIRED
    )
    assert shadow(c["proof"], None, c["strategy"], filters()).intent is None
    from atp.shared.environment import Environment
    from atp.strategy import StrategyEvaluation
    from tests.unit.test_risk_engine import empty_portfolio

    for environment in (Environment.LOCAL, Environment.BACKTEST, Environment.SIMULATION):
        strategy = StrategyEvaluation.completed(
            replace(c["strategy"].provenance, environment=environment), c["strategy"].signal.kind
        )
        result = DeterministicRiskEngine(policy()).evaluate(
            RiskEvaluationContext(
                strategy, market_context(environment=environment.value), empty_portfolio()
            )
        )
        assert result.status.value == "APPROVED"
        assert (
            shadow(c["proof"], result.decision, strategy, filters()).reason_code
            is Reason.TESTNET_ENVIRONMENT_MISMATCH
        )


def test_tq_min_notional_last_price_and_precision():
    payload = symbol_payload()
    payload["filters"].append(
        {
            "filterType": "MIN_NOTIONAL",
            "minNotional": "10",
            "applyToMarket": True,
            "avgPriceMins": 0,
        }
    )
    price = NotionalPriceEvidence(
        "BTCUSDT",
        Decimal("10000"),
        "EXCHANGE_LAST_PRICE",
        ContentIdentity.from_text("last-price-fixture"),
        NOW,
        NOW,
        0,
    )
    assert check_market_filters(filters(payload), "BTCUSDT", Decimal("0.001"), NOW, price) is None
    assert (
        check_market_filters(
            filters(payload),
            "BTCUSDT",
            Decimal("0.001"),
            NOW,
            replace(price, price=Decimal("9999.99999999999999999999")),
        )
        == "SYMBOL_FILTER_INCOMPATIBLE"
    )
    assert (
        check_market_filters(
            filters(payload),
            "BTCUSDT",
            Decimal("0.001"),
            NOW,
            replace(price, source_type="DATA_MARK"),
        )
        == "SYMBOL_FILTER_INCOMPATIBLE"
    )


def test_tq_read_only_query_contract():
    from atp.exchange.read_only import reconciliation_fills_query, reconciliation_order_query

    by_client = ReconciliationLookup("BTCUSDT", client_order_id="atp-fixture")
    by_id = ReconciliationLookup("BTCUSDT", exchange_order_id=1)
    for lookup in (by_client, by_id):
        query = reconciliation_order_query(lookup)
        assert query.method == "GET" and query.path == "/api/v3/order"
        assert query.venue == "BINANCE_SPOT_TESTNET" and not query.submission_authorized
    fills_query = reconciliation_fills_query(by_id)
    assert fills_query.path == "/api/v3/myTrades"
    assert dict(fills_query.parameters)["orderId"] == "1"
    assert reconciliation_fills_query(by_client) is None
    assert reconciliation_order_query(replace(by_client, environment="LIVE")) is None


def test_tq_result_source_binding():
    from atp.testnet_qualification.engine import inspect_qualification

    evidence = evidence_fixture()
    result = qualify(evidence)
    assert inspect_qualification(
        result,
        evidence,
        source_commit_sha=evidence.source_commit_sha,
        repository_identity=evidence.repository_identity,
    )
    assert not inspect_qualification(
        result,
        evidence,
        source_commit_sha="b" * 40,
        repository_identity=evidence.repository_identity,
    )
    object.__setattr__(result, "side_effects_performed", True)
    assert not inspect_qualification(
        result,
        evidence,
        source_commit_sha=evidence.source_commit_sha,
        repository_identity=evidence.repository_identity,
    )
