"""Explicit manual entry point. This release intentionally installs no runtime authorities."""

import argparse
import json

from atp.first_testnet_order.execution import run_first_order
from atp.first_testnet_order.gate import FirstOrderInputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    # No production grant, source/provider authority or credential injection in ENG-TO-001.
    result = run_first_order(FirstOrderInputs(), execute=args.execute)
    print(
        json.dumps(
            {
                "status": result.status,
                "reason_code": result.reason_code.value,
                "transport_call_count": result.transport_call_count,
            },
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
