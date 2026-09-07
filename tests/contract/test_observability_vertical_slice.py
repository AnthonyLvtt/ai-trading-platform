from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atp.accounting import AccountingEngine, AccountingExecution, AccountingReplayInput
from atp.backtesting import (
    BacktestInput,
    DeterministicBacktestEngine,
    ReplayStep,
    SimulatedPositionState,
    SimulationPolicy,
)
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
from atp.observability import (
    AuditJournal,
    EventType,
    ObservabilityStatus,
    ObservationContext,
    observe_accounting_entry,
    observe_accounting_replay,
    observe_backtest,
    observe_data_snapshot,
    observe_risk,
    observe_simulated_fill,
    observe_simulated_order,
    observe_strategy,
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
)
from atp.shared.environment import Environment
from atp.shared.identity import CausationId, ContentIdentity, CorrelationId
from atp.shared.time import LogicalTime
from atp.strategy import (
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluationContext,
    StrategyId,
)


def test_full_vertical_slice_produces_a_verifiable_audit_chain() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = tuple(
        DataPoint.from_value(
            symbol="BTCUSDT",
            value={"close": close, "open": open_price},
            temporal=TemporalMetadata(
                event_time=start + timedelta(minutes=index),
                provider_time=start + timedelta(minutes=index),
                ingested_at=start + timedelta(minutes=index),
                available_at=start + timedelta(minutes=index),
            ),
            finality=DataFinality.FINAL,
        )
        for index, (close, open_price) in enumerate(
            (("3", "3"), ("2", "3"), ("1", "2"), ("4", "1"), ("5", "4.5"))
        )
    )
    snapshot = DatasetSnapshot.create(
        dataset_id=DatasetId("btc-usdt-1m:v1"),
        snapshot_id=SnapshotId("snapshot:observability-contract:v1"),
        source_id=SourceId("historical-observability-fixture"),
        environment=Environment.BACKTEST,
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
        universe_snapshot_id=UniverseSnapshotId("universe:observability-contract:v1"),
        created_at=start,
        effective_at=start,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=(snapshot.snapshot_id,),
        decisions=(SymbolDecision("BTCUSDT", True, "eligible fixture", start),),
    )
    evaluation_time = start + timedelta(minutes=3)
    strategy_engine = SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window=2, long_window=3),
    )
    strategy = strategy_engine.evaluate(
        StrategyEvaluationContext(
            environment=Environment.BACKTEST,
            snapshot=snapshot,
            universe=universe,
            evaluation_time=LogicalTime(evaluation_time),
            symbol="BTCUSDT",
        )
    )
    risk = DeterministicRiskEngine(
        RiskPolicy.v1(policy_id=RiskPolicyId("risk-v1"), version="1.0.0")
    ).evaluate(
        RiskEvaluationContext(
            strategy_evaluation=strategy,
            market_context=RiskMarketContext(
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                position_direction=PositionDirection.LONG,
                margin_enabled=False,
                leverage=Decimal(1),
                instrument_class=InstrumentClass.SPOT,
                environment=Environment.BACKTEST.value,
            ),
            portfolio_state=PortfolioState.create(PortfolioKnowledgeStatus.KNOWN_EMPTY),
        )
    )
    backtest_input = BacktestInput(
        snapshot=snapshot,
        steps=(ReplayStep(strategy, risk, points[3]),),
        initial_state=SimulatedPositionState.empty(),
    )
    backtest = DeterministicBacktestEngine(SimulationPolicy.v1()).replay(backtest_input)
    order = backtest.steps[0].order
    fill = backtest.steps[0].fill
    assert order is not None and fill is not None
    accounting_input = AccountingReplayInput(
        initial_cash=Decimal("100"),
        currency="USDT",
        executions=(AccountingExecution(fill, Decimal("2")),),
    )
    accounting = AccountingEngine().replay(accounting_input)
    entry = accounting.ledger[0]

    correlation = CorrelationId(f"correlation:{backtest.input_identity}")
    journal = AuditJournal.empty()

    def append(result: object) -> None:
        nonlocal journal
        assert hasattr(result, "status")
        assert result.status is ObservabilityStatus.ACCEPTED
        assert result.event is not None
        appended = journal.append(result.event)
        assert appended.status is ObservabilityStatus.ACCEPTED
        assert appended.journal is not None
        journal = appended.journal

    def context() -> ObservationContext:
        if not journal.events:
            return ObservationContext(correlation)
        parent = journal.events[-1]
        return ObservationContext(
            correlation,
            CausationId(str(parent.event_id)),
            parent.content_identity,
        )

    append(
        observe_data_snapshot(
            snapshot,
            accepted=True,
            reason_code=None,
            context=context(),
        )
    )
    append(observe_strategy(strategy, context=context()))
    append(
        observe_risk(
            risk,
            strategy_evaluation=strategy,
            context=context(),
        )
    )
    append(observe_simulated_order(order, environment=Environment.BACKTEST, context=context()))
    append(observe_simulated_fill(fill, environment=Environment.BACKTEST, context=context()))
    append(
        observe_backtest(
            backtest,
            replay_input=backtest_input,
            context=context(),
        )
    )
    append(observe_accounting_entry(entry, environment=Environment.BACKTEST, context=context()))
    append(
        observe_accounting_replay(
            accounting,
            replay_input=accounting_input,
            environment=Environment.BACKTEST,
            context=context(),
        )
    )

    assert journal.validate().status is ObservabilityStatus.ACCEPTED
    assert {event.correlation_id for event in journal.events} == {correlation}
    assert [event.event_type for event in journal.events] == [
        EventType.DATA_SNAPSHOT_ACCEPTED,
        EventType.STRATEGY_EVALUATED,
        EventType.RISK_DECISION_PRODUCED,
        EventType.SIMULATED_ORDER_CREATED,
        EventType.SIMULATED_FILL_PRODUCED,
        EventType.BACKTEST_COMPLETED,
        EventType.ACCOUNTING_ENTRY_APPLIED,
        EventType.ACCOUNTING_REPLAY_COMPLETED,
    ]
    assert all(event.classification.value == "INTERNAL" for event in journal.events)
    assert journal.events[0].occurred_at == snapshot.created_at
    assert journal.events[1].occurred_at == strategy.provenance.evaluation_time.value
    assert journal.events[2].occurred_at == strategy.provenance.evaluation_time.value
    assert journal.events[3].occurred_at == order.created_at
    assert journal.events[4].occurred_at == fill.fill_time
    assert journal.events[5].occurred_at == fill.fill_time
    assert journal.events[6].occurred_at == entry.provenance.fill_time
    assert journal.events[7].occurred_at == entry.provenance.fill_time

    forged_strategy_time = strategy_engine.evaluate(
        StrategyEvaluationContext(
            environment=Environment.BACKTEST,
            snapshot=snapshot,
            universe=universe,
            evaluation_time=LogicalTime(start + timedelta(minutes=2)),
            symbol="BTCUSDT",
        )
    )
    forged_time = observe_risk(
        risk,
        strategy_evaluation=forged_strategy_time,
        context=ObservationContext(correlation),
    )
    assert forged_time.status is ObservabilityStatus.BLOCKED
    assert forged_time.event is None


def test_observability_has_no_forbidden_authority_or_infrastructure_imports() -> None:
    forbidden = (
        "atp.oms",
        "atp.exchange",
        "atp.persistence",
        "openai",
        "requests",
        "httpx",
        "socket",
        "kafka",
        "opentelemetry",
        "prometheus",
        "elasticsearch",
    )
    for module_path in Path("src/atp/observability").glob("*.py"):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        imported.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert all(not name.startswith(forbidden) for name in imported), module_path


def test_observability_adapters_fail_closed_on_malformed_domain_object() -> None:
    result = observe_strategy(
        object(),
        context=ObservationContext(
            CorrelationId(f"correlation:{ContentIdentity.from_text('malformed')}")
        ),
    )

    assert result.status is ObservabilityStatus.BLOCKED
    assert result.event is None
