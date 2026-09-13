"""Explicitly provisioned local SQLite ledger, durable atomic consumption, no reset API."""

import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from atp.exchange.read_only import safe_json
from atp.first_testnet_order.model import SubmissionState
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes


class LedgerUnavailable(ValueError):
    pass


class AlreadyConsumed(ValueError):
    pass


class TestnetSubmissionLedger:
    __test__ = False

    def __init__(self, path: Path) -> None:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or path.is_symlink()
            or ":" in str(path)
            or str(path).startswith("//")
        ):
            raise LedgerUnavailable("Local ledger required")
        self.path = path

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_text("ATP_FIRST_ORDER_LEDGER_V1:" + str(self.path.resolve()))

    def __repr__(self) -> str:
        return "TestnetSubmissionLedger(<local>)"

    @classmethod
    def create(cls, path: Path) -> "TestnetSubmissionLedger":
        ledger = cls(path)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(ledger._connect()) as db:
                db.executescript("""
                    CREATE TABLE records (
                        sequence INTEGER PRIMARY KEY,
                        authorization TEXT NOT NULL, client TEXT NOT NULL,
                        state TEXT NOT NULL, evidence TEXT, previous TEXT NOT NULL,
                        identity TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX reservation ON records(authorization)
                        WHERE state = 'ATTEMPT_STARTED';
                    CREATE UNIQUE INDEX client_reservation ON records(client)
                        WHERE state = 'ATTEMPT_STARTED';
                    CREATE TRIGGER no_update BEFORE UPDATE ON records
                        BEGIN SELECT RAISE(ABORT, 'append only'); END;
                    CREATE TRIGGER no_delete BEFORE DELETE ON records
                        BEGIN SELECT RAISE(ABORT, 'append only'); END;
                """)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, sqlite3.Error):
            raise LedgerUnavailable("Ledger provisioning failed") from None
        return ledger

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file() or self.path.is_symlink():
            raise LedgerUnavailable("Ledger unavailable")
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _inspect(db: sqlite3.Connection) -> list[tuple[str, str, str, str | None, str, str]]:
        rows = db.execute(
            "SELECT authorization,client,state,evidence,previous,identity "
            "FROM records ORDER BY sequence"
        ).fetchall()
        previous = "GENESIS"
        states: dict[str, str] = {}
        clients: dict[str, str] = {}
        allowed: dict[str | None, set[str]] = {
            None: {SubmissionState.ATTEMPT_STARTED},
            SubmissionState.ATTEMPT_STARTED: {
                SubmissionState.ACKNOWLEDGED,
                SubmissionState.UNKNOWN,
                SubmissionState.RECONCILED,
            },
            SubmissionState.ACKNOWLEDGED: {SubmissionState.UNKNOWN, SubmissionState.RECONCILED},
            SubmissionState.UNKNOWN: {SubmissionState.UNKNOWN, SubmissionState.RECONCILED},
            SubmissionState.RECONCILED: {SubmissionState.RECONCILED},
        }
        for auth, client, state, evidence, prev, identity in rows:
            payload = [auth, client, state, evidence, prev]
            if (
                prev != previous
                or identity != str(ContentIdentity.from_canonical(payload))
                or state not in allowed.get(states.get(auth), set())
                or (client in clients and clients[client] != auth)
            ):
                raise LedgerUnavailable("Ledger integrity failure")
            if evidence is not None:
                safe_json(json.loads(evidence))
            previous, states[auth], clients[client] = identity, state, auth
        return rows

    def consumed(self, authorization: ContentIdentity, client: str) -> bool:
        try:
            with closing(self._connect()) as db:
                return any(
                    row[0] == str(authorization) or row[1] == client for row in self._inspect(db)
                )
        except (OSError, sqlite3.Error, ValueError):
            raise LedgerUnavailable("Ledger unavailable") from None

    def append(
        self,
        authorization: ContentIdentity,
        client: str,
        state: SubmissionState,
        evidence: bytes | None = None,
        before_reservation: Callable[[], None] | None = None,
    ) -> None:
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                rows = self._inspect(db)
                matching = [r for r in rows if r[0] == str(authorization) or r[1] == client]
                if state is SubmissionState.ATTEMPT_STARTED and matching:
                    raise AlreadyConsumed("Authorization consumed")
                if state is not SubmissionState.ATTEMPT_STARTED and not matching:
                    raise LedgerUnavailable("No reserved attempt")
                if state is SubmissionState.ATTEMPT_STARTED and before_reservation is not None:
                    before_reservation()
                prev = rows[-1][-1] if rows else "GENESIS"
                data = (
                    None
                    if evidence is None
                    else canonical_json_bytes(json.loads(evidence)).decode()
                )
                if data is not None:
                    safe_json(json.loads(data))
                payload = [str(authorization), client, state.value, data, prev]
                identity = str(ContentIdentity.from_canonical(payload))
                db.execute(
                    "INSERT INTO records(authorization,client,state,evidence,previous,identity) "
                    "VALUES (?,?,?,?,?,?)",
                    (*payload, identity),
                )
                self._inspect(db)
                db.commit()  # FULL synchronous: durable reservation before caller gets permission.
        except (OSError, sqlite3.Error, ValueError) as error:
            if isinstance(error, AlreadyConsumed):
                raise
            raise LedgerUnavailable("Ledger unavailable") from None

    def established_order_id(self, authorization: ContentIdentity, client: str) -> str | None:
        """Read the established exchange ID from validated append-only history."""
        try:
            with closing(self._connect()) as db:
                ids = set()
                for auth, cid, _, evidence, _, _ in self._inspect(db):
                    if auth == str(authorization) and cid == client and evidence is not None:
                        value = json.loads(evidence).get("exchange_order_id")
                        if type(value) in (str, int):
                            ids.add(str(value))
                if len(ids) > 1:
                    raise LedgerUnavailable("Inconsistent exchange order")
                return next(iter(ids), None)
        except (OSError, sqlite3.Error, ValueError, AttributeError):
            raise LedgerUnavailable("Ledger unavailable") from None
