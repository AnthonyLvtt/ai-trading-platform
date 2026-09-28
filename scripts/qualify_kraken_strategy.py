"""Deterministic OFFLINE_CONTRACT runner for canonical Kraken Strategy evaluation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from atp.data.market_data import ingest_closed_public_candles
from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.kraken import parse_asset_pairs, parse_closed_ohlc
from atp.exchange.read_only import encoded
from atp.kraken_strategy_qualification import qualify_offline
from atp.shared.environment import Environment

FIXTURE_OBSERVED_AT = datetime(2026, 9, 24, 12, 0, 1, tzinfo=UTC)


def _fixture(name: str) -> object:
    return json.loads((Path("tests/fixtures/kraken") / name).read_text())


def main() -> int:
    mapping, _ = parse_asset_pairs(_fixture("asset-pairs.json"), BTC_EUR, FIXTURE_OBSERVED_AT)
    candles = parse_closed_ohlc(
        _fixture("ohlc-strategy-51.json"), BTC_EUR, mapping, 5, FIXTURE_OBSERVED_AT
    )
    batch = ingest_closed_public_candles(
        selected_venue=VenueId.KRAKEN,
        instrument=BTC_EUR,
        mapping=mapping,
        candle_evidence=candles,
        interval_minutes=5,
        environment=Environment.TEST,
    )
    result = qualify_offline(batch.snapshot)
    document = dict(encoded(result)) | {"content_identity": str(result.content_identity)}
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0 if result.status.value == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
