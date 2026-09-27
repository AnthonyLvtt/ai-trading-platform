"""Explicit Kraken public Market Data qualification runner.

OFFLINE_CONTRACT uses repository fixtures. PUBLIC_CONNECTIVITY is credential-free,
GET-only and must be selected explicitly.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from atp.exchange.contracts import BTC_EUR
from atp.exchange.kraken import (
    KrakenPublicClient,
    KrakenPublicHTTPTransport,
    parse_asset_pairs,
    parse_closed_ohlc,
)
from atp.exchange.read_only import encoded
from atp.kraken_market_data_qualification import qualify_offline, qualify_public_connectivity

FIXTURE_OBSERVED_AT = datetime(2026, 9, 24, 12, 0, 1, tzinfo=UTC)


def _fixture(name: str) -> object:
    return json.loads((Path("tests/fixtures/kraken") / name).read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--level", choices=("OFFLINE_CONTRACT", "PUBLIC_CONNECTIVITY"), required=True
    )
    args = parser.parse_args()
    if args.level == "OFFLINE_CONTRACT":
        mapping, _ = parse_asset_pairs(_fixture("asset-pairs.json"), BTC_EUR, FIXTURE_OBSERVED_AT)
        candles = parse_closed_ohlc(_fixture("ohlc.json"), BTC_EUR, mapping, 5, FIXTURE_OBSERVED_AT)
        result = qualify_offline(BTC_EUR, mapping, candles)
    else:
        result = qualify_public_connectivity(KrakenPublicClient(KrakenPublicHTTPTransport()))
    document = dict(encoded(result)) | {"content_identity": str(result.content_identity)}
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0 if result.status.value == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
