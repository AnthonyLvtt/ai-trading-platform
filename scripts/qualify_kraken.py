"""Explicit Kraken PUBLIC_CONNECTIVITY qualification runner.

This script has no credentials, private routes, economic calls or venue fallback.
"""

from __future__ import annotations

import argparse
import json

from atp.exchange.kraken import KrakenPublicClient, KrakenPublicHTTPTransport
from atp.exchange.read_only import encoded
from atp.kraken_qualification import qualify_public_connectivity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", choices=("PUBLIC_CONNECTIVITY",), required=True)
    parser.parse_args()
    result = qualify_public_connectivity(KrakenPublicClient(KrakenPublicHTTPTransport()))
    document = dict(encoded(result)) | {"content_identity": str(result.content_identity)}
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0 if result.status.value == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
