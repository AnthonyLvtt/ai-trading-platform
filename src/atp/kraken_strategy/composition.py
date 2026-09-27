"""Fail-closed Kraken DATA to venue-neutral Strategy composition."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import cast

from atp.data import (
    DatasetSnapshot,
    SymbolDecision,
    UniverseSnapshot,
    UniverseSnapshotId,
)
from atp.data.market_data import (
    KRAKEN_CANDLE_DATASET_ID,
    KRAKEN_CANDLE_INTERVAL_MINUTES,
    KRAKEN_CANDLE_LINEAGE_OPERATION,
    KRAKEN_CANDLE_SCHEMA_VERSION,
    KRAKEN_CANDLE_SOURCE_ID,
    KRAKEN_CANDLE_TRANSFORMATION_VERSION,
)
from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.read_only import EvidenceError
from atp.shared.environment import ACTIVE_ENVIRONMENTS
from atp.shared.identity import ContentIdentity
from atp.shared.time import LogicalTime
from atp.strategy import (
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluation,
    StrategyEvaluationContext,
    StrategyId,
)

KRAKEN_STRATEGY_RULES_VERSION = "ENG-STRAT-KRAKEN-001/1.0"
KRAKEN_STRATEGY_SYMBOL = "BTC/EUR"
KRAKEN_STRATEGY_INTERVAL = "5m"
KRAKEN_STRATEGY_REASON = "KRAKEN_CANONICAL_DATA_ELIGIBLE"
_CANONICAL_CANDLE_FIELDS = frozenset(
    {
        "close",
        "high",
        "instrument",
        "interval",
        "low",
        "open",
        "open_time",
        "trade_count",
        "venue",
        "volume",
    }
)


def _payload(point_payload: bytes) -> dict[str, object]:
    try:
        value = cast(object, json.loads(point_payload))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceError("KRAKEN_STRATEGY_INPUT_INVALID") from None
    if type(value) is not dict:
        raise EvidenceError("KRAKEN_STRATEGY_INPUT_INVALID")
    return value


def _validate_snapshot(snapshot: DatasetSnapshot) -> None:
    step = timedelta(minutes=KRAKEN_CANDLE_INTERVAL_MINUTES)
    if (
        type(snapshot) is not DatasetSnapshot
        or snapshot.environment not in ACTIVE_ENVIRONMENTS
        or snapshot.dataset_id != KRAKEN_CANDLE_DATASET_ID
        or snapshot.source_id != KRAKEN_CANDLE_SOURCE_ID
        or snapshot.schema_version != KRAKEN_CANDLE_SCHEMA_VERSION
        or snapshot.transformation_version != KRAKEN_CANDLE_TRANSFORMATION_VERSION
        or not snapshot.points
        or len(snapshot.lineage.steps) != 1
        or snapshot.lineage.steps[0].operation != KRAKEN_CANDLE_LINEAGE_OPERATION
        or snapshot.lineage.steps[0].version != KRAKEN_CANDLE_TRANSFORMATION_VERSION
        or not snapshot.lineage.steps[0].input_identities
    ):
        raise EvidenceError("KRAKEN_STRATEGY_INPUT_INVALID")

    previous = None
    for point in snapshot.points:
        payload = _payload(point.canonical_payload)
        if (
            point.symbol != KRAKEN_STRATEGY_SYMBOL
            or set(payload) != _CANONICAL_CANDLE_FIELDS
            or payload.get("venue") != VenueId.KRAKEN.value
            or payload.get("instrument")
            != {
                "base_asset": BTC_EUR.base_asset,
                "market": BTC_EUR.market.value,
                "quote_asset": BTC_EUR.quote_asset,
            }
            or payload.get("interval") != KRAKEN_STRATEGY_INTERVAL
            or point.temporal.provider_time is None
            or payload.get("open_time") != point.temporal.provider_time.isoformat()
            or point.temporal.event_time - point.temporal.provider_time != step
            or (previous is not None and point.temporal.event_time - previous != step)
        ):
            raise EvidenceError("KRAKEN_STRATEGY_INPUT_INVALID")
        previous = point.temporal.event_time


def _universe_manifest(snapshot: DatasetSnapshot) -> dict[str, str]:
    return {
        "dataset_id": str(snapshot.dataset_id),
        "effective_at": snapshot.created_at.isoformat(),
        "environment": snapshot.environment.value,
        "kind": "kraken_strategy_universe",
        "rules_version": KRAKEN_STRATEGY_RULES_VERSION,
        "snapshot_content_identity": str(snapshot.content_identity),
        "snapshot_id": str(snapshot.snapshot_id),
        "symbol": KRAKEN_STRATEGY_SYMBOL,
    }


def compose_kraken_universe(snapshot: DatasetSnapshot) -> UniverseSnapshot:
    """Build the one-symbol analytical universe bound to one exact Kraken snapshot."""

    _validate_snapshot(snapshot)
    identity = ContentIdentity.from_canonical(_universe_manifest(snapshot))
    return UniverseSnapshot.create(
        universe_snapshot_id=UniverseSnapshotId(f"kraken-strategy-universe:{identity}"),
        created_at=snapshot.created_at,
        effective_at=snapshot.created_at,
        rules_version=KRAKEN_STRATEGY_RULES_VERSION,
        source_snapshot_ids=(snapshot.snapshot_id,),
        decisions=(
            SymbolDecision(
                KRAKEN_STRATEGY_SYMBOL,
                True,
                KRAKEN_STRATEGY_REASON,
                snapshot.created_at,
            ),
        ),
    )


def evaluate_kraken_strategy(
    snapshot: DatasetSnapshot,
    universe: UniverseSnapshot,
) -> StrategyEvaluation:
    """Evaluate the accepted 20/50 SMA without adding runtime or economic authority."""

    _validate_snapshot(snapshot)
    if universe != compose_kraken_universe(snapshot):
        raise EvidenceError("KRAKEN_STRATEGY_UNIVERSE_INVALID")
    strategy = SmaCrossoverStrategy(
        StrategyId("sma-crossover"),
        "1.0.0",
        SmaCrossoverConfig(short_window=20, long_window=50),
    )
    return strategy.evaluate(
        StrategyEvaluationContext(
            environment=snapshot.environment,
            snapshot=snapshot,
            universe=universe,
            evaluation_time=LogicalTime(snapshot.created_at),
            symbol=KRAKEN_STRATEGY_SYMBOL,
            runtime_authorization=None,
            candle_interval=KRAKEN_STRATEGY_INTERVAL,
        )
    )
