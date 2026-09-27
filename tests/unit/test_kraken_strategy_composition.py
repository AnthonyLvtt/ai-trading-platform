from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atp.data import DataFinality, DataPoint, DatasetSnapshot
from atp.data.market_data import ingest_closed_public_candles
from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.kraken import parse_asset_pairs, parse_closed_ohlc
from atp.exchange.read_only import EvidenceError
from atp.kraken_strategy import (
    KRAKEN_STRATEGY_INTERVAL,
    KRAKEN_STRATEGY_RULES_VERSION,
    KRAKEN_STRATEGY_SYMBOL,
    compose_kraken_universe,
    evaluate_kraken_strategy,
)
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from atp.strategy import EvaluationStatus, ReasonCode, SignalKind

FIXTURES = Path("tests/fixtures/kraken")
AT = datetime(2026, 9, 24, 12, 0, 1, tzinfo=UTC)


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def raw_ohlc(signal: SignalKind, *, closed_count: int = 51) -> dict[str, object]:
    payload = fixture("ohlc-strategy-51.json")
    assert isinstance(payload, dict)
    result = payload["result"]
    assert isinstance(result, dict)
    rows = result["XXBTZEUR"]
    assert isinstance(rows, list)
    if signal is SignalKind.EXIT:
        rows[-2][1:7] = ["100.0", "100.0", "1.0", "1.0", "1.0", "1.0"]
        rows[-1][1:7] = ["1.0", "1.0", "1.0", "1.0", "1.0", "1.0"]
    elif signal is SignalKind.NO_ACTION:
        for row in rows:
            row[1:7] = ["100.0"] * 6
    if closed_count == 50:
        result["XXBTZEUR"] = rows[1:]
    return payload


def batch(
    signal: SignalKind = SignalKind.LONG_ENTRY,
    *,
    closed_count: int = 51,
    environment: Environment = Environment.TEST,
):
    mapping, _ = parse_asset_pairs(fixture("asset-pairs.json"), BTC_EUR, AT)
    candles = parse_closed_ohlc(
        raw_ohlc(signal, closed_count=closed_count), BTC_EUR, mapping, 5, AT
    )
    return ingest_closed_public_candles(
        selected_venue=VenueId.KRAKEN,
        instrument=BTC_EUR,
        mapping=mapping,
        candle_evidence=candles,
        interval_minutes=5,
        environment=environment,
    )


def rebuild(snapshot: DatasetSnapshot, points: tuple[DataPoint, ...]) -> DatasetSnapshot:
    return DatasetSnapshot.create(
        dataset_id=snapshot.dataset_id,
        snapshot_id=snapshot.snapshot_id,
        source_id=snapshot.source_id,
        environment=snapshot.environment,
        schema_version=snapshot.schema_version,
        transformation_version=snapshot.transformation_version,
        created_at=snapshot.created_at,
        points=points,
        quality=snapshot.quality,
        freshness=snapshot.freshness,
        gap_status=snapshot.gap_status,
        gaps=snapshot.gaps,
        degradation_reasons=snapshot.degradation_reasons,
        lineage=snapshot.lineage,
    )


def test_raw_fixture_yields_exactly_51_closed_candles() -> None:
    raw = raw_ohlc(SignalKind.LONG_ENTRY)
    result = raw["result"]
    assert isinstance(result, dict)
    rows = result["XXBTZEUR"]
    assert isinstance(rows, list)
    assert len(rows) == 52
    mapping, _ = parse_asset_pairs(fixture("asset-pairs.json"), BTC_EUR, AT)
    parsed = parse_closed_ohlc(raw, BTC_EUR, mapping, 5, AT)
    assert len(parsed.candles) == 51


@pytest.mark.parametrize("signal", [SignalKind.LONG_ENTRY, SignalKind.EXIT, SignalKind.NO_ACTION])
def test_existing_sma_produces_expected_signal(signal: SignalKind) -> None:
    snapshot = batch(signal).snapshot
    result = evaluate_kraken_strategy(snapshot, compose_kraken_universe(snapshot))
    assert result.status is EvaluationStatus.COMPLETED
    assert result.signal is not None
    assert result.signal.kind is signal


def test_50_closed_points_are_insufficient_for_20_50() -> None:
    snapshot = batch(closed_count=50).snapshot
    result = evaluate_kraken_strategy(snapshot, compose_kraken_universe(snapshot))
    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.INSUFFICIENT_HISTORY
    assert result.signal is None


def test_universe_id_uses_exact_acyclic_manifest_and_evaluation_time_rule() -> None:
    snapshot = batch().snapshot
    universe = compose_kraken_universe(snapshot)
    manifest = {
        "dataset_id": str(snapshot.dataset_id),
        "effective_at": snapshot.created_at.isoformat(),
        "environment": snapshot.environment.value,
        "kind": "kraken_strategy_universe",
        "rules_version": KRAKEN_STRATEGY_RULES_VERSION,
        "snapshot_content_identity": str(snapshot.content_identity),
        "snapshot_id": str(snapshot.snapshot_id),
        "symbol": KRAKEN_STRATEGY_SYMBOL,
    }
    identity = ContentIdentity.from_canonical(manifest)
    assert str(universe.universe_snapshot_id) == f"kraken-strategy-universe:{identity}"
    evaluation = evaluate_kraken_strategy(snapshot, universe)
    assert evaluation.provenance.evaluation_time.value == snapshot.created_at
    assert evaluation.provenance.candle_interval == KRAKEN_STRATEGY_INTERVAL
    assert "candle_interval" not in evaluation.provenance.canonical_value()


def test_non_final_visible_point_is_rejected_by_existing_strategy_contract() -> None:
    snapshot = batch().snapshot
    points = list(snapshot.points)
    points[-1] = replace(points[-1], finality=DataFinality.PROVISIONAL)
    changed = rebuild(snapshot, tuple(points))
    result = evaluate_kraken_strategy(changed, compose_kraken_universe(changed))
    assert result.status is EvaluationStatus.BLOCKED_INPUT
    assert result.reason_code is ReasonCode.DATA_CONTRACT_UNSATISFIED


@pytest.mark.parametrize(
    "field,value",
    [
        ("dataset_id", "foreign-dataset"),
        ("source_id", "foreign-source"),
        ("schema_version", "foreign-schema"),
        ("transformation_version", "foreign-transformation"),
        ("environment", Environment.DRY_RUN),
        ("environment", Environment.TESTNET),
        ("environment", Environment.LIVE),
    ],
)
def test_incompatible_snapshot_or_environment_fails_closed(field: str, value: object) -> None:
    from atp.data import DatasetId, SourceId

    snapshot = batch().snapshot
    if field == "dataset_id":
        value = DatasetId(str(value))
    elif field == "source_id":
        value = SourceId(str(value))
    with pytest.raises(EvidenceError, match="KRAKEN_STRATEGY_INPUT_INVALID"):
        compose_kraken_universe(replace(snapshot, **{field: value}))


@pytest.mark.parametrize("change", ["symbol", "interval", "cadence", "instrument", "venue"])
def test_wrong_canonical_market_contract_fails_closed(change: str) -> None:
    snapshot = batch().snapshot
    points = list(snapshot.points)
    point = points[-1]
    payload = json.loads(point.canonical_payload)
    if change == "symbol":
        points[-1] = replace(point, symbol="BTCUSDT")
    elif change == "interval":
        payload["interval"] = "1m"
        points[-1] = DataPoint.from_value(
            symbol=point.symbol,
            value=payload,
            temporal=point.temporal,
            finality=point.finality,
        )
    elif change == "cadence":
        changed_event_time = point.temporal.event_time + timedelta(minutes=1)
        points[-1] = replace(
            point,
            temporal=replace(
                point.temporal,
                event_time=changed_event_time,
                available_at=changed_event_time,
            ),
        )
    elif change == "instrument":
        payload["instrument"]["quote_asset"] = "USDT"
        points[-1] = DataPoint.from_value(
            symbol=point.symbol,
            value=payload,
            temporal=point.temporal,
            finality=point.finality,
        )
    else:
        payload["venue"] = "BINANCE"
        points[-1] = DataPoint.from_value(
            symbol=point.symbol,
            value=payload,
            temporal=point.temporal,
            finality=point.finality,
        )
    changed = rebuild(snapshot, tuple(points))
    with pytest.raises(EvidenceError, match="KRAKEN_STRATEGY_INPUT_INVALID"):
        compose_kraken_universe(changed)


def test_foreign_universe_fails_closed() -> None:
    snapshot = batch().snapshot
    universe = compose_kraken_universe(snapshot)
    foreign = replace(universe, rules_version="foreign-rules")
    with pytest.raises(EvidenceError, match="KRAKEN_STRATEGY_UNIVERSE_INVALID"):
        evaluate_kraken_strategy(snapshot, foreign)


def test_kraken_strategy_identities_are_deterministic_and_alias_free() -> None:
    snapshot = batch().snapshot
    universe = compose_kraken_universe(snapshot)
    first = evaluate_kraken_strategy(snapshot, universe)
    second = evaluate_kraken_strategy(snapshot, universe)
    assert first == second
    assert first.provenance.content_identity == second.provenance.content_identity
    assert first.strategy_evaluation_id == second.strategy_evaluation_id
    rendered = repr(universe) + repr(first)
    for alias in ("XBT", "XXBT", "ZEUR", "XXBTZEUR", "XBTEUR"):
        assert alias not in rendered


def test_existing_btcusdt_strategy_identity_and_behavior_are_unchanged() -> None:
    from tests.unit.test_strategy_baseline import context, snapshot, strategy

    result = strategy().evaluate(context(snapshot()))
    assert result.signal is not None
    assert result.signal.kind is SignalKind.LONG_ENTRY
    expected_evaluation_id = (
        "strategy-evaluation:"
        "sha256:c2d36c58850230b548ae69ce1b84d3b045cadbb774dc639576c78425ca3d5939"
    )
    assert str(result.strategy_evaluation_id) == expected_evaluation_id
    assert (
        str(result.content_identity)
        == "sha256:f9e688bb90068c60ed849a869b601d7a48cb51e568305c4f1ea4fc0df3c023c4"
    )
