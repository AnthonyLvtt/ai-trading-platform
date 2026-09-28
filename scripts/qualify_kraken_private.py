"""Run fixture-driven Kraken private read-only OFFLINE_CONTRACT qualification."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.private import (
    account_wide_open_orders_request,
    parse_api_key_info,
    parse_balances,
    parse_open_orders,
)
from atp.exchange.private_contracts import PrivateCredentialReference
from atp.exchange.read_only import encoded
from atp.kraken_private_qualification import qualify_private_offline
from atp.shared.environment import Environment


def _load(directory: Path, name: str) -> object:
    return json.loads((directory / name).read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", choices=("OFFLINE_CONTRACT",), required=True)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("tests/fixtures/kraken/private"),
    )
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    observed_at = datetime(2026, 9, 28, 12, tzinfo=UTC)
    reference = PrivateCredentialReference(VenueId.KRAKEN, "0" * 32, Environment.TEST)
    capability = parse_api_key_info(
        _load(args.fixtures, "api-key-info.json"), reference, observed_at
    )
    balances = parse_balances(
        _load(args.fixtures, "balance.json"), reference, capability, observed_at
    )
    open_orders_request = account_wide_open_orders_request(reference)
    open_orders = parse_open_orders(
        _load(args.fixtures, "open-orders.json"),
        reference,
        capability,
        open_orders_request,
        observed_at,
    )
    result = qualify_private_offline(
        reference,
        capability,
        balances,
        open_orders_request,
        open_orders,
        source_root=args.source_root,
    )
    document = dict(encoded(result)) | {"content_identity": str(result.content_identity)}
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0 if result.status.value == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
