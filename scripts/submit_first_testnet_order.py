"""Operator-only preparation. This command has no economic execution composition."""

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atp.exchange.read_only import EvidenceError, encoded
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.preparation_http import TestnetReadOnlySource
from atp.first_testnet_order.preparation_runtime import prepare_check_only
from atp.release_deployment.source import inspect_source
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.runtime_credentials import (
    ReferencedEnvironmentCredentialsProvider,
    RuntimeCredentialCapabilityAuthority,
    TestnetCredentialPermissionAttestation,
    TrustedCredentialPermissionAuthority,
    new_credential_reference,
)


def read_external_pin(args, kind):
    if getattr(args, "prepare", False) and kind == "first-order-authorization":
        return None
    option = (
        "activation_grant_pin" if kind == "activation-grant" else "first_order_authorization_pin"
    )
    value = getattr(args, option, None)
    if value is None and getattr(args, "request_trust_pins", False) and sys.stdin.isatty():
        try:
            value = input(f"External approved pin for {kind} (blank = stop): ")
        except EOFError:
            return None
    if type(value) is not str or not value:
        return None
    try:
        algorithm, digest = value.split(":", 1)
        return ContentIdentity(algorithm, digest)
    except (ValueError, ValidationError):
        return None


def run(args):
    if (
        not (args.check_only or getattr(args, "prepare", False))
        or not args.confirm_testnet_permissions
    ):
        return {
            "status": "BLOCKED",
            "reason_code": "FIRST_ORDER_AUTHORIZATION_REQUIRED",
            "real_economic_calls": 0,
            "LIVE": "LIVE_FORBIDDEN",
        }
    root = Path.cwd().resolve()
    session = args.session_dir.resolve()
    if root == session or root in session.parents or session.exists():
        raise EvidenceError("INVALID_SESSION_DIRECTORY")
    reference = new_credential_reference()
    provider = ReferencedEnvironmentCredentialsProvider(reference)
    verified_at = datetime.now(UTC)
    # The session snapshot retains material; build/test subprocesses cannot inherit it.
    for name in ("ATP_BINANCE_TESTNET_API_KEY", "ATP_BINANCE_TESTNET_API_SECRET"):
        os.environ.pop(name, None)
    if not provider.credentials_present():
        raise EvidenceError("CREDENTIAL_CAPABILITY_INVALID")
    session.mkdir(mode=0o700, parents=True)
    (session / "artifacts").mkdir(mode=0o700)
    from qualify_testnet import collect
    from release import run as release_run

    releases, qualifications = [], []
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = release_run(
            root,
            args.release_version,
            session / "release",
            args.source_commit,
            on_qualified=lambda bundle, wheel: releases.append((bundle, wheel)),
        )
        if code or len(releases) != 1:
            raise EvidenceError("RELEASE_BINDING_REQUIRED")
        code = collect(
            session / "tq.json",
            on_qualified=lambda result, evidence: qualifications.append((result, evidence)),
        )
        if code or len(qualifications) != 1:
            raise EvidenceError("TESTNET_QUALIFICATION_INVALID")
    if inspect_source(root) != releases[0][0].source:
        raise EvidenceError("RELEASE_BINDING_REQUIRED")
    # Explicit manual confirmation is the out-of-band attestation, never account inference.
    attestation = TestnetCredentialPermissionAttestation(
        reference.credential_reference_id,
        True,
        True,
        "OPERATOR",
        verified_at,
        verified_at + timedelta(hours=24),
    )
    authority = RuntimeCredentialCapabilityAuthority(
        provider,
        attestation,
        TrustedCredentialPermissionAuthority(attestation.content_identity),
        lambda: datetime.now(UTC),
    )
    capability = authority.attest(reference.content_identity)

    def save(name, artifact):
        # Private session directory, outside source and release artifacts. No reusable seal.
        document = {
            "content": encoded(artifact),
            "content_identity": str(artifact.content_identity),
        }
        path = session / (name + ".json")
        with path.open("x") as stream:
            json.dump(document, stream, sort_keys=True)
        path.chmod(0o600)
        if name in ("activation-grant", "first-order-authorization"):
            print(
                json.dumps(
                    {
                        "status": "BLOCKED",
                        "reason_code": "TRUST_PIN_REQUIRED",
                        "candidate": name,
                        "content_identity": str(artifact.content_identity),
                        "real_economic_calls": 0,
                    }
                ),
                flush=True,
            )

    save("permission-attestation", attestation)
    report = prepare_check_only(
        source=TestnetReadOnlySource(provider),
        now=lambda: datetime.now(UTC),
        release=releases[0][0],
        wheel=releases[0][1],
        tq=qualifications[0][0],
        tq_evidence=qualifications[0][1],
        credential_source_identity=reference.content_identity,
        credential_authority=authority,
        workspace=session,
        ledger=TestnetSubmissionLedger.create(session / "submission.sqlite"),
        artifact_sink=save,
        trust_pin_source=lambda kind: read_external_pin(args, kind),
    )
    report.update(
        credential_reference_id=reference.credential_reference_id,
        permission_attestation_identity=str(attestation.content_identity),
        capability_evidence_identity=str(capability.content_identity),
    )
    if inspect_source(root) != releases[0][0].source:
        raise EvidenceError("RELEASE_BINDING_REQUIRED")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true", help="Unavailable in this composition")
    parser.add_argument("--source-commit")
    parser.add_argument("--release-version")
    parser.add_argument("--session-dir", type=Path)
    parser.add_argument("--activation-grant-pin")
    parser.add_argument("--first-order-authorization-pin")
    parser.add_argument(
        "--request-trust-pins",
        action="store_true",
        help="Pause for independently approved pins in the current terminal session",
    )
    parser.add_argument(
        "--confirm-testnet-permissions",
        action="store_true",
        help="Attest verified Testnet Spot trading and absent withdrawal capability",
    )
    args = parser.parse_args()
    if (
        (args.check_only or args.prepare)
        and args.confirm_testnet_permissions
        and not all((args.source_commit, args.release_version, args.session_dir))
    ):
        parser.error("source-commit, release-version and session-dir are required")
    try:
        report = run(args)
    except (ValueError, OSError) as exc:
        # Never interpolate an exception crossing a credential/network boundary.
        reason = "FIRST_ORDER_NOT_READY"
        safe_reasons = {
            "NO_ADMISSIBLE_QUANTITY",
            "OPEN_ORDERS_EVIDENCE_INVALID",
            "OPEN_ORDERS_EVIDENCE_STALE",
            "CREDENTIAL_CAPABILITY_INVALID",
            "INVALID_SESSION_DIRECTORY",
            "RELEASE_BINDING_REQUIRED",
            "TESTNET_QUALIFICATION_INVALID",
            "TIME_EVIDENCE_INVALID",
            "INVALID_FILTER_EVIDENCE",
            "PRICE_EVIDENCE_STALE",
            "PORTFOLIO_STATE_UNKNOWN",
            "READ_ONLY_SOURCE_UNAVAILABLE",
        }
        if (
            isinstance(exc, EvidenceError)
            and len(exc.args) == 1
            and type(exc.args[0]) is str
            and exc.args[0] in safe_reasons
        ):
            reason = exc.args[0]
        report = {
            "status": "BLOCKED",
            "reason_code": reason,
            "real_economic_calls": 0,
            "LIVE": "LIVE_FORBIDDEN",
        }
    report["transport_call_count"] = 0
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READY_TO_SUBMIT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
