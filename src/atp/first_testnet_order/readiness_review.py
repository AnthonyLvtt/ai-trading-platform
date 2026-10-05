"""Non-economic, fail-closed review of the first Testnet campaign prerequisites.

This offline review cannot turn historical or absent session evidence into authority.
The existing preparation watcher alone can create READY_FOR_CTO_REVIEW after a
genuine LONG_ENTRY and all of its fresh in-process gates.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

from atp.exchange.model import ExchangePolicy
from atp.first_testnet_order.controlled import OPERATIONAL_LEDGER_PATH
from atp.first_testnet_order.ledger import TestnetSubmissionLedger
from atp.first_testnet_order.model import FIRST_ORDER_QUOTE_CAP
from atp.release_deployment.model import ReleaseManifest, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity
from atp.testnet_qualification.model import (
    CASES,
    TestnetQualificationPolicy,
    TestnetQualificationSuiteResult,
)

_SCHEMA = {
    ("table", "records"): """CREATE TABLE records (
        sequence INTEGER PRIMARY KEY,
        authorization TEXT NOT NULL, client TEXT NOT NULL,
        state TEXT NOT NULL, evidence TEXT, previous TEXT NOT NULL,
        identity TEXT NOT NULL
    )""",
    ("index", "reservation"): """CREATE UNIQUE INDEX reservation ON records(authorization)
        WHERE state = 'ATTEMPT_STARTED'""",
    ("index", "client_reservation"): """CREATE UNIQUE INDEX client_reservation ON records(client)
        WHERE state = 'ATTEMPT_STARTED'""",
    ("trigger", "no_update"): """CREATE TRIGGER no_update BEFORE UPDATE ON records
        BEGIN SELECT RAISE(ABORT, 'append only'); END""",
    ("trigger", "no_delete"): """CREATE TRIGGER no_delete BEFORE DELETE ON records
        BEGIN SELECT RAISE(ABORT, 'append only'); END""",
}


class ReadinessUnavailable(ValueError):
    """Closed reason code; no filesystem or database detail crosses the report boundary."""


def _document(path: Path) -> dict[str, object]:
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ReadinessUnavailable("EVIDENCE_INVALID")
        value = json.loads(path.read_text())
        if type(value) is not dict:
            raise ReadinessUnavailable("EVIDENCE_INVALID")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReadinessUnavailable("EVIDENCE_INVALID") from None


def _bound_identity(
    path: Path,
    source: SourceTree,
    record_type: type[ReleaseManifest] | type[TestnetQualificationSuiteResult],
) -> str:
    data = _document(path)
    try:
        values = {f.name: data[f.name] for f in fields(record_type) if f.name != "content_identity"}
        identity = str(ContentIdentity.from_canonical(values))
        if (
            data.get("content_identity") != identity
            or data.get("source_commit_sha") != source.source_commit_sha
            or data.get("repository_identity") != str(source.repository_identity)
        ):
            raise ReadinessUnavailable("EVIDENCE_INVALID")
        if record_type is ReleaseManifest:
            if data.get("source_branch") != "main" or data.get("auto_start_allowed") is not False:
                raise ReadinessUnavailable("RELEASE_BINDING_INVALID")
        else:
            cases = data.get("cases")
            required = [case.case_id for case in CASES]
            evidence = data.get("evidence")
            if (
                data.get("status") != "PASSED"
                or data.get("reason_code") != "TESTNET_QUALIFICATION_PASSED"
                or data.get("level") != "OFFLINE_CONTRACT"
                or data.get("side_effects_performed") is not False
                or data.get("adapter_identity") != str(ExchangePolicy().content_identity)
                or data.get("policy_identity") != str(TestnetQualificationPolicy().content_identity)
                or data.get("required_cases") != required
                or not isinstance(cases, list)
                or [case.get("case_id") for case in cases] != required
                or any(case.get("status") != "PASSED" for case in cases)
                or not isinstance(evidence, dict)
                or evidence.get("source_commit_sha") != source.source_commit_sha
                or evidence.get("repository_identity") != str(source.repository_identity)
                or data.get("evidence_identity") != str(ContentIdentity.from_canonical(evidence))
            ):
                raise ReadinessUnavailable("TQ_BINDING_INVALID")
        return identity
    except ReadinessUnavailable:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ReadinessUnavailable("EVIDENCE_INVALID") from None


def _schema(db: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (kind, name): " ".join(sql.split())
        for kind, name, sql in db.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN "
            "('table','index','trigger') AND name NOT LIKE 'sqlite_%'"
        )
    }


def inspect_campaign_read_only(path: Path, *, durable_root: Path | None = None) -> str:
    """Inspect the fixed campaign without a write-capable SQLite connection."""
    root = Path.home() if durable_root is None else durable_root
    if not isinstance(path, Path) or not path.is_absolute() or not path.is_relative_to(root):
        raise ReadinessUnavailable("LEDGER_UNAVAILABLE")
    try:
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ReadinessUnavailable("LEDGER_UNAVAILABLE")
        directory, file = path.parent.stat(), path.stat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or not stat.S_ISREG(file.st_mode)
            or directory.st_uid != os.getuid()
            or file.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
            or stat.S_IMODE(file.st_mode) != 0o600
            or directory.st_dev != root.stat().st_dev
            or file.st_dev != directory.st_dev
        ):
            raise ReadinessUnavailable("LEDGER_UNAVAILABLE")
        uri = path.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ReadinessUnavailable("LEDGER_INTEGRITY_INVALID")
            if _schema(db) != {key: " ".join(sql.split()) for key, sql in _SCHEMA.items()}:
                raise ReadinessUnavailable("LEDGER_SCHEMA_INVALID")
            if db.execute("SELECT COUNT(*) FROM records").fetchone() != (0,):
                raise ReadinessUnavailable("FIRST_ORDER_ALREADY_CONSUMED")
        return str(TestnetSubmissionLedger(path).content_identity)
    except (OSError, sqlite3.Error):
        raise ReadinessUnavailable("LEDGER_UNAVAILABLE") from None


def review_readiness(
    source_root: Path,
    expected_sha: str,
    *,
    ledger_path: Path = OPERATIONAL_LEDGER_PATH,
    release_manifest_path: Path | None = None,
    tq_result_path: Path | None = None,
    source_inspector: Callable[[Path], SourceTree] = inspect_source,
    ledger_inspector: Callable[[Path], str] = inspect_campaign_read_only,
) -> dict[str, object]:
    """Return deterministic NO_GO until fresh, same-session runtime evidence exists.

    This command has no credential, grant, pin, watcher or economic transport port.
    It cannot assert READY_FOR_CTO_REVIEW from identity strings or old JSON files.
    """
    report: dict[str, object] = {
        "status": "NO_GO",
        "reason_code": "FRESH_RUNTIME_EVIDENCE_REQUIRED",
        "source_sha": None,
        "source_tree_identity": None,
        "release_identity": None,
        "tq_identity": None,
        "campaign_ledger_identity": None,
        "campaign_ledger_state": "UNKNOWN",
        "credential_reference_identity": None,
        "activation_grant_identity": None,
        "first_order_authorization_candidate_identity": None,
        "filter_evidence_identity": None,
        "account_evidence_identity": None,
        "open_orders_evidence_identity": None,
        "risk_identity": None,
        "quote_cap": str(FIRST_ORDER_QUOTE_CAP),
        "real_economic_calls": 0,
        "side_effect_performed": False,
        "LIVE": "LIVE_FORBIDDEN",
    }
    source: SourceTree | None = None
    try:
        source = source_inspector(source_root)
        if not isinstance(source, SourceTree) or (
            source.source_commit_sha != expected_sha
            or source.source_branch != "main"
            or source.clean is not True
        ):
            report["reason_code"] = "SOURCE_BINDING_INVALID"
        else:
            report["source_sha"] = source.source_commit_sha
            report["source_tree_identity"] = str(source.content_identity)
    except (OSError, ValueError):
        report["reason_code"] = "SOURCE_BINDING_INVALID"
    if source is not None and report["source_tree_identity"] is not None:
        for path, kind, key in (
            (release_manifest_path, ReleaseManifest, "release_identity"),
            (tq_result_path, TestnetQualificationSuiteResult, "tq_identity"),
        ):
            if path is not None:
                try:
                    report[key] = _bound_identity(path, source, kind)
                except ReadinessUnavailable as exc:
                    report["reason_code"] = str(exc)
    try:
        report["campaign_ledger_identity"] = ledger_inspector(ledger_path)
        report["campaign_ledger_state"] = "NOT_ATTEMPTED"
    except ReadinessUnavailable as exc:
        report["campaign_ledger_state"] = "UNKNOWN"
        report["reason_code"] = str(exc)
    report["artifact_identity"] = str(ContentIdentity.from_canonical(report))
    return report
