"""Print a deterministic, offline first-order readiness review; never authorize execution."""

import argparse
import json
from pathlib import Path

from atp.first_testnet_order.readiness_review import review_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--tq-result", type=Path)
    args = parser.parse_args()
    report = review_readiness(
        Path.cwd().resolve(),
        args.expected_source_sha,
        release_manifest_path=args.release_manifest,
        tq_result_path=args.tq_result,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_CTO_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
