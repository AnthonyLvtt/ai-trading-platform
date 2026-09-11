from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from atp.data import (
    DataFinality,
    DataLineage,
    DataPoint,
    DataQuality,
    DatasetId,
    DatasetSnapshot,
    FreshnessStatus,
    GapStatus,
    LineageStep,
    SnapshotId,
    SourceId,
    SymbolDecision,
    TemporalMetadata,
    UniverseSnapshot,
    UniverseSnapshotId,
)
from atp.risk import (
    DeterministicRiskEngine,
    InstrumentClass,
    MarketType,
    PortfolioKnowledgeStatus,
    PortfolioState,
    PositionDirection,
    RiskEvaluationContext,
    RiskMarketContext,
    RiskPolicy,
    RiskPolicyId,
    RiskStatus,
)
from atp.shared.environment import Environment
from atp.shared.time import LogicalTime
from atp.strategy import (
    SignalKind,
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluationContext,
    StrategyId,
)


def test_real_data_strategy_risk_to_fake_exchange(tmp_path, monkeypatch) -> None:
    from tests.unit.test_exchange import no_network

    guard = no_network.__wrapped__(monkeypatch)
    del guard
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = tuple(
        DataPoint.from_value(
            symbol="BTCUSDT",
            value={"close": close},
            temporal=TemporalMetadata(
                event_time=start + timedelta(minutes=index),
                provider_time=start + timedelta(minutes=index),
                ingested_at=start + timedelta(minutes=index),
                available_at=start + timedelta(minutes=index),
            ),
            finality=DataFinality.FINAL,
        )
        for index, close in enumerate(("3", "2", "1", "4"))
    )
    data = DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:risk-contract:v1"),
        source_id=SourceId("historical-risk-fixture"),
        environment=Environment.TEST,
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        created_at=start + timedelta(minutes=3),
        points=points,
        quality=DataQuality.VALID,
        freshness=FreshnessStatus.FRESH,
        gap_status=GapStatus.NO_GAP_DETECTED,
        gaps=(),
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("normalize", "v1"),)),
    )
    universe = UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId("universe:risk-contract:v1"),
        created_at=start,
        effective_at=start,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(data.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "eligible fixture", start),),
    )
    strategy = SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
    )
    strategy_result = strategy.evaluate(
        StrategyEvaluationContext(
            environment=Environment.TEST,
            snapshot=data,
            universe=universe,
            evaluation_time=LogicalTime(start + timedelta(minutes=3)),
            symbol="BTCUSDT",
        )
    )
    assert strategy_result.signal is not None
    assert strategy_result.signal.kind is SignalKind.LONG_ENTRY
    risk_context = RiskEvaluationContext(
        strategy_evaluation=strategy_result,
        market_context=RiskMarketContext(
            symbol="BTCUSDT",
            market_type=MarketType.SPOT,
            position_direction=PositionDirection.LONG,
            margin_enabled=False,
            leverage=Decimal(1),
            instrument_class=InstrumentClass.SPOT,
            environment=Environment.TEST.value,
        ),
        portfolio_state=PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY),
    )
    engine = DeterministicRiskEngine(
        RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    )

    first = engine.evaluate(risk_context)
    second = engine.evaluate(risk_context)

    assert first == second
    assert first.status is RiskStatus.APPROVED
    assert first.decision is not None
    assert first.provenance.strategy_evaluation_id == strategy_result.strategy_evaluation_id
    assert first.provenance.strategy_evaluation_identity == strategy_result.content_identity
    assert first.provenance.strategy_signal_identity == strategy_result.signal.content_identity
    assert first.risk_decision_id is not None

    from atp.exchange import (
        ExchangeAdapter,
        Reason,
        Side,
        Status,
        UpstreamOrderProof,
        authorize_testnet,
        request_from_proof,
    )
    from atp.ops import observability_evidence, readiness
    from tests.exchange_support import FakeOnlyAdapter, FakeTransport, fixture_auth, record
    from tests.unit.test_ops import config

    (tmp_path / "artifacts").mkdir()
    ops = readiness(config(tmp_path, "TESTNET"), observability=observability_evidence())
    assert ops.reason_code.value == "TESTNET_NOT_AUTHORIZED"
    assert authorize_testnet(ops) is None
    risk_testnet = engine.evaluate(
        RiskEvaluationContext(
            strategy_result,
            RiskMarketContext(
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                position_direction=PositionDirection.LONG,
                margin_enabled=False,
                leverage=Decimal(1),
                instrument_class=InstrumentClass.SPOT,
                environment="TESTNET",
            ),
            PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY),
        )
    )
    assert risk_testnet.status is RiskStatus.BLOCKED
    assert risk_testnet.reason_code.value == "ENVIRONMENT_NOT_ACTIVE"
    proof = record(
        UpstreamOrderProof,
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=Decimal("0.001"),
        risk_decision_id=str(first.decision.risk_decision_id),
        risk_decision_identity=first.decision.content_identity,
        strategy_evaluation_identity=strategy_result.content_identity,
        environment="TESTNET",
        created_at=start + timedelta(minutes=3),
    )
    order = request_from_proof(proof)
    args = dict(
        proof=proof,
        risk=first.decision,
        strategy=strategy_result,
        authorization=fixture_auth(),
        readiness=ops,
        submitted_at=proof.created_at,
    )
    fake = FakeTransport()
    assert FakeOnlyAdapter(fake).submit(order, **args).status is Status.ACCEPTED
    calls = len(fake.calls)
    assert (
        ExchangeAdapter(fake).submit(order, **args).reason_code is Reason.RISK_AUTHORIZATION_INVALID
    )
    assert len(fake.calls) == calls
