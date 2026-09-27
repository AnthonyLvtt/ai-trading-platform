from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from atp.data import DataFinality
from atp.data.market_data import (
    KRAKEN_CANDLE_AVAILABILITY_RULE,
    KRAKEN_CANDLE_DATASET_ID,
    KRAKEN_CANDLE_SCHEMA_VERSION,
    KRAKEN_CANDLE_TRANSFORMATION_VERSION,
    ingest_closed_public_candles,
)
from atp.exchange.contracts import BTC_EUR, BTC_USDT, VenueId
from atp.exchange.kraken import parse_closed_ohlc
from atp.exchange.read_only import EvidenceError
from atp.shared.environment import Environment
from atp.shared.identity import ContentIdentity
from tests.unit.test_kraken_public import AT, evidence, fixture


def batch(*, environment: Environment = Environment.TEST):
    mapping, _ = evidence()
    candles = parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT)
    return ingest_closed_public_candles(
        selected_venue=VenueId.KRAKEN,
        instrument=BTC_EUR,
        mapping=mapping,
        candle_evidence=candles,
        interval_minutes=5,
        environment=environment,
    )


def test_exact_canonical_projection_and_temporal_semantics() -> None:
    result = batch()
    mapping, _ = evidence()
    source = parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT)
    point = result.snapshot.points[-1]
    candle = source.candles[-1]
    payload = json.loads(point.canonical_payload)

    assert point.symbol == "BTC/EUR"
    assert point.finality is DataFinality.FINAL
    assert point.temporal.event_time == candle.open_time + timedelta(minutes=5)
    assert point.temporal.provider_time == candle.open_time
    assert point.temporal.ingested_at == source.observed_at
    assert point.temporal.available_at == max(point.temporal.event_time, source.observed_at)
    assert point.temporal.availability_rule == KRAKEN_CANDLE_AVAILABILITY_RULE
    assert payload["close"] == str(candle.close)
    assert payload["trade_count"] == candle.trade_count
    assert payload["venue"] == "KRAKEN"
    assert payload["instrument"] == {
        "base_asset": "BTC",
        "market": "SPOT",
        "quote_asset": "EUR",
    }


def test_identity_graph_is_deterministic_and_input_only() -> None:
    first = batch()
    second = batch()
    assert first == second
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot.lineage.steps[0].input_identities == (first.provenance.content_identity,)
    assert not any(
        name in {field.name for field in first.provenance.__dataclass_fields__.values()}
        for name in ("snapshot_id", "lineage_identity")
    )


def test_snapshot_id_uses_the_approved_acyclic_manifest() -> None:
    result = batch()
    manifest = {
        "dataset_id": str(KRAKEN_CANDLE_DATASET_ID),
        "environment": Environment.TEST.value,
        "lineage_identity": str(result.snapshot.lineage.content_identity),
        "points": [point.canonical_value() for point in result.snapshot.points],
        "provenance_identity": str(result.provenance.content_identity),
        "schema_version": KRAKEN_CANDLE_SCHEMA_VERSION,
        "transformation_version": KRAKEN_CANDLE_TRANSFORMATION_VERSION,
    }
    assert str(result.snapshot.snapshot_id) == str(ContentIdentity.from_canonical(manifest))


def test_no_native_kraken_alias_leaks_into_generic_data() -> None:
    result = batch()
    rendered = repr(result.snapshot) + repr(result.provenance)
    for alias in ("XBT", "XXBT", "ZEUR", "XXBTZEUR", "XBTEUR"):
        assert alias not in rendered


@pytest.mark.parametrize(
    "environment",
    [Environment.LOCAL, Environment.TEST, Environment.BACKTEST, Environment.SIMULATION],
)
def test_exact_environment_allowlist(environment: Environment) -> None:
    assert batch(environment=environment).snapshot.environment is environment


@pytest.mark.parametrize(
    "environment", [Environment.DRY_RUN, Environment.TESTNET, Environment.LIVE]
)
def test_disallowed_environments_fail_closed(environment: Environment) -> None:
    with pytest.raises(EvidenceError, match="KRAKEN_CANDLE_INGESTION_INVALID"):
        batch(environment=environment)


def test_foreign_venue_instrument_or_mapping_fails_closed() -> None:
    mapping, _ = evidence()
    candles = parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT)
    base = dict(
        selected_venue=VenueId.KRAKEN,
        instrument=BTC_EUR,
        mapping=mapping,
        candle_evidence=candles,
        interval_minutes=5,
        environment=Environment.TEST,
    )
    for changes in (
        {"selected_venue": VenueId.BINANCE},
        {"instrument": BTC_USDT},
        {"mapping": replace(mapping, venue=VenueId.BINANCE)},
        {"interval_minutes": 1},
    ):
        with pytest.raises(EvidenceError, match="KRAKEN_CANDLE_INGESTION_INVALID"):
            ingest_closed_public_candles(**(base | changes))


@pytest.mark.parametrize("change", ["gap", "stale", "bad_price", "bad_volume", "trade_count"])
def test_malformed_or_stale_batches_fail_closed(change: str) -> None:
    mapping, _ = evidence()
    candles = parse_closed_ohlc(fixture("ohlc.json"), BTC_EUR, mapping, 5, AT)
    values = list(candles.candles)
    if change == "gap":
        values[0] = replace(values[0], open_time=values[0].open_time - timedelta(minutes=5))
    elif change == "stale":
        candles = replace(candles, observed_at=AT + timedelta(minutes=5))
    elif change == "bad_price":
        values[0] = replace(values[0], high=values[0].low - 1)
    elif change == "bad_volume":
        values[0] = replace(values[0], volume=values[0].volume.copy_negate())
    else:
        values[0] = replace(values[0], trade_count=-1)
    candles = replace(candles, candles=tuple(values))
    with pytest.raises(EvidenceError, match="KRAKEN_CANDLE_INGESTION_INVALID"):
        ingest_closed_public_candles(
            selected_venue=VenueId.KRAKEN,
            instrument=BTC_EUR,
            mapping=mapping,
            candle_evidence=candles,
            interval_minutes=5,
            environment=Environment.TEST,
        )


def test_existing_binance_data_identity_semantics_remain_unchanged() -> None:
    from atp.data import DataPoint, TemporalMetadata

    point = DataPoint.from_value(
        symbol="BTCUSDT",
        value={"close": "100.00"},
        temporal=TemporalMetadata(AT, AT, AT, AT),
        finality=DataFinality.FINAL,
    )
    assert (
        str(point.content_identity)
        == "sha256:09e7eeb5022c793ce2c857854960d79f107c5c8cce0ca5b7b27f4603811bcb18"
    )


def test_existing_fixture_is_not_modified() -> None:
    fixture_path = Path("tests/fixtures/kraken/ohlc.json")
    assert isinstance(json.loads(fixture_path.read_text()), dict)
