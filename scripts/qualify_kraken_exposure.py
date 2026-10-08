"""Explicit operator command for source-bound Kraken exposure observation qualification.

This command is network-capable but never invoked by ordinary runtime or CI.
Execution still requires separate operational authorization tied to the exact
merged main SHA supplied with --expected-source-sha.
"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from atp.exchange.kraken.exposure_transport import KrakenExposureHTTPTransport
from atp.exchange.kraken.private_credentials import load_interactive_credential
from atp.exchange.kraken.private_transport import KrakenPrivateHTTPTransport
from atp.exchange.read_only import encoded
from atp.kraken_private_qualification.exposure_gate import (
    account_identity_from_iiban,
    qualify_exposure_operator_gate,
)
from atp.kraken_private_qualification.exposure_gate_model import ExposureGateStatus

CONFIRMATION = "KRAKEN_READ_ONLY_EXPOSURE_OBSERVATION"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    iiban = getpass.getpass("Approved Kraken Spot account IIBAN: ")
    account_identity = account_identity_from_iiban(iiban)
    iiban = ""

    confirmation = getpass.getpass(
        f"Type {CONFIRMATION} to execute exactly three bounded private reads: "
    )
    if confirmation != CONFIRMATION:
        parser.error("explicit read-only confirmation required")
    confirmation = ""

    result = qualify_exposure_operator_gate(
        expected_source_sha=args.expected_source_sha,
        source_root=args.source_root,
        expected_account_identity=account_identity,
        credential_loader=load_interactive_credential,
        key_info_transport=KrakenPrivateHTTPTransport(),
        exposure_transport=KrakenExposureHTTPTransport(),
    )
    document = dict(encoded(result)) | {"content_identity": str(result.content_identity)}
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    if result.status is ExposureGateStatus.PASSED:
        return 0
    if result.status is ExposureGateStatus.INCOMPLETE:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
