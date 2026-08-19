from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta

import pytest

from atp.data import (
    DataFinality,
    DataLineage,
    DataPoint,
    DataQuality,
    DatasetId,
    DatasetSnapshot,
    FreshnessStatus,
    Gap,
    GapStatus,
    LineageStep,
    SnapshotId,
    SourceId,
    SymbolDecision,
    TemporalMetadata,
    UniverseSnapshot,
    UniverseSnapshotId,
)
from atp.shared.environment import Environment
from atp.shared.errors import ValidationError
from atp.shared.time import LogicalTime
from atp.strategy import (
    EvaluationStatus,
    ReasonCode,
    SignalKind,
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyDecisionId,
    StrategyEvaluation,
    StrategyEvaluationContext,
    StrategyEvaluationId,
    StrategyId,
    StrategySignal,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def candle(
    close: object,
    index: int,
    *,
    symbol: str = "BTCUSDT",
    finality: DataFinality = DataFinality.FINAL,
    available_at: datetime | None = None,
) -> DataPoint:
    event_time = T0 + timedelta(minutes=index)
    availability = event_time if available_at is None else available_at
    return DataPoint.from_value(
        symbol=symbol,
        value={"close": close},
        temporal=TemporalMetadata(
            event_time=event_time,
            provider_time=event_time,
            ingested_at=availability,
            available_at=availability,
        ),
        finality=finality,
    )


def snapshot(
    closes: tuple[object, ...] = ("3", "2", "1", "4"),
    *,
    dataset_id: str = "btc-usdt-1m:v1",
    snapshot_id: str = "snapshot-strategy-1",
    environment: Environment = Environment.BACKTEST,
    quality: DataQuality = DataQuality.VALID,
    freshness: FreshnessStatus = FreshnessStatus.FRESH,
    gap_status: GapStatus = GapStatus.NO_GAP_DETECTED,
    finality: DataFinality = DataFinality.FINAL,
    extra_points: tuple[DataPoint, ...] = (),
) -> DatasetSnapshot:
    gaps = (
        (Gap(T0, T0 + timedelta(minutes=1), "missing candle"),)
        if gap_status is GapStatus.KNOWN_GAP
        else ()
    )
    return DatasetSnapshot.create(
        dataset_id=DatasetId(dataset_id),
        snapshot_id=SnapshotId(snapshot_id),
        source_id=SourceId("historical-strategy-fixture"),
        environment=environment,
        schema_version="candle-v1",
        transformation_version="normalize-v1",
        created_at=T0 + timedelta(minutes=10),
        points=tuple(candle(close, index, finality=finality) for index, close in enumerate(closes))
        + extra_points,
        quality=quality,
        freshness=freshness,
        gap_status=gap_status,
        gaps=gaps,
        degradation_reasons=frozenset(),
        lineage=DataLineage((LineageStep("normalize", "v1"),)),
    )


def universe(
    data: DatasetSnapshot,
    *,
    universe_id: str = "universe-strategy-1",
    symbol: str = "BTCUSDT",
    eligible: bool = True,
    effective_at: datetime | None = None,
    source_snapshot_ids: tuple[SnapshotId, ...] | None = None,
) -> UniverseSnapshot:
    effective = effective_at or T0
    return UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId(universe_id),
        created_at=effective,
        effective_at=effective,
        rules_version="spot-usdt-v1",
        source_snapshot_ids=source_snapshot_ids or (data.snapshot_id,),
        decisions=(SymbolDecision(symbol, eligible, "fixture eligibility", effective),),
    )


def strategy(*, short_window: int = 2, long_window: int = 3) -> SmaCrossoverStrategy:
    return SmaCrossoverStrategy(
        strategy_id=StrategyId("sma-crossover"),
        version="1.0.0",
        configuration=SmaCrossoverConfig(short_window, long_window),
    )


def context(
    data: DatasetSnapshot,
    *,
    market_universe: UniverseSnapshot | None = None,
    evaluation_time: datetime | None = None,
    environment: Environment = Environment.BACKTEST,
    symbol: str = "BTCUSDT",
) -> StrategyEvaluationContext:
    return StrategyEvaluationContext(
        environment=environment,
        snapshot=data,
        universe=market_universe or universe(data),
        evaluation_time=LogicalTime(
            evaluation_time or T0 + timedelta(minutes=len(data.points) - 1)
        ),
        symbol=symbol,
    )


def assert_signal(result: StrategyEvaluation, expected: SignalKind) -> None:
    assert result.status is EvaluationStatus.COMPLETED
    assert result.reason_code is None
    assert result.signal is not None
    assert result.signal.kind is expected


@pytest.mark.parametrize(
    ("short_window", "long_window"),
    [
        (0, 2),
        (-1, 2),
        (2, 2),
        (3, 2),
        (True, 2),
        (1, True),
        (1.5, 3),
        (1, 3.5),
    ],
)
def test_configuration_rejects_invalid_windows(short_window: object, long_window: object) -> None:
    with pytest.raises(ValidationError):
        SmaCrossoverConfig(short_window, long_window)  # type: ignore[arg-type]


def test_configuration_is_immutable() -> None:
    configuration = SmaCrossoverConfig(2, 3)

    with pytest.raises(FrozenInstanceError):
        configuration.short_window = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        (("3", "2", "1", "4"), SignalKind.LONG_ENTRY),
        (("1", "2", "3", "0"), SignalKind.EXIT),
        (("1", "2", "3", "4"), SignalKind.NO_ACTION),
    ],
)
def test_sma_crossover_signal_semantics(closes: tuple[object, ...], expected: SignalKind) -> None:
    data = snapshot(closes)

    result = strategy().evaluate(context(data))

    assert_signal(result, expected)
    assert result.signal is not None
    if expected is SignalKind.NO_ACTION:
        assert result.signal.strategy_decision_id is None
    else:
        assert result.signal.strategy_decision_id is not None


def test_insufficient_history_is_blocked_without_decision() -> None:
    data = snapshot(("3", "2", "1"))

    result = strategy().evaluate(context(data))

    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.INSUFFICIENT_HISTORY
    assert result.signal is None


def test_identical_input_is_deterministic() -> None:
    data = snapshot()
    evaluation_context = context(data)

    first = strategy().evaluate(evaluation_context)
    second = strategy().evaluate(evaluation_context)

    assert first == second
    assert first.content_identity == second.content_identity
    assert first.strategy_evaluation_id == second.strategy_evaluation_id


def test_strategy_identifiers_remain_distinct_types() -> None:
    assert StrategyId("same") != StrategyEvaluationId("same")
    assert StrategyEvaluationId("same") != StrategyDecisionId("same")


def test_configuration_change_changes_provenance() -> None:
    data = snapshot(("4", "3", "2", "1", "5"))
    evaluation_context = context(data)

    first = strategy(short_window=1, long_window=2).evaluate(evaluation_context)
    second = strategy(short_window=2, long_window=3).evaluate(evaluation_context)

    assert first.provenance.configuration.content_identity != (
        second.provenance.configuration.content_identity
    )
    assert first.content_identity != second.content_identity


def test_future_data_is_not_visible() -> None:
    evaluation_time = T0 + timedelta(minutes=3)
    future = candle("-100", 4, available_at=T0 + timedelta(minutes=4))
    data = snapshot(extra_points=(future,))

    result = strategy().evaluate(context(data, evaluation_time=evaluation_time))

    assert_signal(result, SignalKind.LONG_ENTRY)
    assert all(used.available_at <= evaluation_time for used in result.provenance.used_data)
    assert future.content_identity not in {
        used.content_identity for used in result.provenance.used_data
    }


@pytest.mark.parametrize(
    "data",
    [
        snapshot(quality=DataQuality.INVALID, snapshot_id="invalid-quality"),
        snapshot(freshness=FreshnessStatus.STALE, snapshot_id="stale"),
        snapshot(gap_status=GapStatus.KNOWN_GAP, snapshot_id="known-gap"),
        snapshot(finality=DataFinality.PROVISIONAL, snapshot_id="provisional"),
    ],
)
def test_incompatible_data_contract_is_blocked(data: DatasetSnapshot) -> None:
    result = strategy().evaluate(context(data))

    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.DATA_CONTRACT_UNSATISFIED
    assert result.signal is None


def test_snapshot_environment_mismatch_is_blocked() -> None:
    data = snapshot(environment=Environment.TEST)

    result = strategy().evaluate(context(data, environment=Environment.BACKTEST))

    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.SNAPSHOT_INCOMPATIBLE


@pytest.mark.parametrize("eligible", [False])
def test_ineligible_symbol_is_blocked(eligible: bool) -> None:
    data = snapshot()
    market_universe = universe(data, eligible=eligible)

    result = strategy().evaluate(context(data, market_universe=market_universe))

    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.UNIVERSE_INCOMPATIBLE


def test_unlinked_universe_is_blocked() -> None:
    data = snapshot()
    market_universe = universe(data, source_snapshot_ids=(SnapshotId("different-snapshot"),))

    result = strategy().evaluate(context(data, market_universe=market_universe))

    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.UNIVERSE_INCOMPATIBLE


def test_invalid_close_is_blocked_without_substitution() -> None:
    data = snapshot(("3", "2", "1", "not-a-price"))

    result = strategy().evaluate(context(data))

    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.INVALID_CLOSE
    assert result.signal is None


def test_signal_identity_and_provenance_are_reproducible() -> None:
    data = snapshot(dataset_id="candles:v7", snapshot_id="snapshot:v4")
    market_universe = universe(data, universe_id="universe:v9")
    evaluation_context = context(data, market_universe=market_universe)

    result = strategy().evaluate(evaluation_context)
    repeated = strategy().evaluate(evaluation_context)

    assert result.signal is not None
    assert repeated.signal is not None
    assert result.signal.content_identity == repeated.signal.content_identity
    assert result.provenance.dataset_id == DatasetId("candles:v7")
    assert result.provenance.snapshot_id == SnapshotId("snapshot:v4")
    assert result.provenance.universe_snapshot_id == UniverseSnapshotId("universe:v9")
    assert result.provenance.used_data


def test_signal_rejects_a_forged_decision_identity() -> None:
    result = strategy().evaluate(context(snapshot()))
    assert result.signal is not None

    with pytest.raises(ValidationError, match="decision identity"):
        replace(
            result.signal,
            strategy_decision_id=StrategyDecisionId("strategy-decision:forged"),
        )


def test_evaluation_rejects_a_forged_evaluation_identity() -> None:
    result = strategy().evaluate(context(snapshot()))

    with pytest.raises(ValidationError, match="evaluation identity"):
        replace(
            result,
            strategy_evaluation_id=StrategyEvaluationId("strategy-evaluation:forged"),
        )


def test_strategy_has_no_order_or_quantity_contract() -> None:
    forbidden = {"order", "quantity", "position_size", "size"}

    assert forbidden.isdisjoint(field.name for field in fields(StrategySignal))
    assert forbidden.isdisjoint(field.name for field in fields(StrategyEvaluation))


def test_strategy_logic_does_not_read_system_time() -> None:
    source = inspect.getsource(SmaCrossoverStrategy)

    assert "datetime.now" not in source
    assert "utc_now" not in source
