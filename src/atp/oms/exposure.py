"""Offline OMS exposure evidence and durable budget reservations.

No assessment, order submission, network operation, or runtime eligibility is
implemented here. A reservation is accounting state, not trading authority.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceRecord
from atp.shared.identity import ContentIdentity


class ExposureError(ValueError):
    pass


def _nonnegative(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value >= 0


def _exact_seconds(delta: timedelta) -> Decimal:
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(
        1_000_000
    )


@dataclass(frozen=True, slots=True)
class SpendableEurEvidence(EvidenceRecord):
    venue: VenueId
    account_identity: ContentIdentity
    credential_reference_identity: ContentIdentity
    balance_source_identity: ContentIdentity
    amount_eur: Decimal
    observed_at: datetime
    qualification_identity: ContentIdentity
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or any(
                type(value) is not ContentIdentity
                for value in (
                    self.account_identity,
                    self.credential_reference_identity,
                    self.balance_source_identity,
                    self.qualification_identity,
                )
            )
            or not _nonnegative(self.amount_eur)
            or type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.side_effect_performed is not False
        ):
            raise ExposureError("SPENDABLE_EUR_EVIDENCE_INVALID")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class BoundedFeeEvidence(EvidenceRecord):
    venue: VenueId
    account_identity: ContentIdentity
    candidate_identity: ContentIdentity
    maximum_fee_eur: Decimal
    observed_at: datetime
    source_identity: ContentIdentity
    qualification_identity: ContentIdentity
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or any(
                type(value) is not ContentIdentity
                for value in (
                    self.account_identity,
                    self.candidate_identity,
                    self.source_identity,
                    self.qualification_identity,
                )
            )
            or not _nonnegative(self.maximum_fee_eur)
            or type(self.observed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.side_effect_performed is not False
        ):
            raise ExposureError("BOUNDED_FEE_EVIDENCE_INVALID")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class FreshnessEvidence(EvidenceRecord):
    subject_identity: ContentIdentity
    observed_at: datetime
    assessed_at: datetime
    maximum_age_seconds: Decimal
    policy_identity: ContentIdentity
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.subject_identity) is not ContentIdentity
            or type(self.policy_identity) is not ContentIdentity
            or type(self.observed_at) is not datetime
            or type(self.assessed_at) is not datetime
            or self.observed_at.tzinfo is None
            or self.assessed_at.tzinfo is None
            or type(self.maximum_age_seconds) is not Decimal
            or not self.maximum_age_seconds.is_finite()
            or self.maximum_age_seconds <= 0
            or self.assessed_at < self.observed_at
            or _exact_seconds(self.assessed_at - self.observed_at) > self.maximum_age_seconds
            or self.side_effect_performed is not False
        ):
            raise ExposureError("FRESHNESS_EVIDENCE_INVALID")
        EvidenceRecord.__post_init__(self)


class ExposureReservationLedger:
    """Atomic, account-wide, reserve-only offline budget ledger.

    Reservations never expire or release automatically. This keeps uncertainty
    blocked until a separately qualified reconciliation/release policy exists.
    """

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or (path.exists() and path.is_symlink()):
            raise ExposureError("RESERVATION_PATH_INVALID")
        self._path = path

    def reserve(
        self,
        *,
        account_identity: ContentIdentity,
        candidate_identity: ContentIdentity,
        budget_eur: Decimal,
        amount_eur: Decimal,
    ) -> bool:
        """Reserve only if all previous holds plus this amount fit the budget."""
        if (
            type(account_identity) is not ContentIdentity
            or type(candidate_identity) is not ContentIdentity
            or not _nonnegative(budget_eur)
            or not _nonnegative(amount_eur)
            or amount_eur == 0
        ):
            raise ExposureError("RESERVATION_INPUT_INVALID")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self._path, timeout=10) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS exposure_reservations ("
                    "account_identity TEXT NOT NULL, "
                    "candidate_identity TEXT NOT NULL PRIMARY KEY, "
                    "amount_eur TEXT NOT NULL)"
                )
                rows = db.execute(
                    "SELECT account_identity, candidate_identity, amount_eur "
                    "FROM exposure_reservations"
                ).fetchall()
                total = Fraction(0)
                for account, candidate, raw_amount in rows:
                    if (
                        type(account) is not str
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", account)
                        or type(candidate) is not str
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate)
                        or type(raw_amount) is not str
                    ):
                        raise ExposureError("RESERVATION_HISTORY_INVALID")
                    try:
                        previous_amount = Decimal(raw_amount)
                    except InvalidOperation:
                        raise ExposureError("RESERVATION_HISTORY_INVALID") from None
                    if not _nonnegative(previous_amount) or previous_amount == 0:
                        raise ExposureError("RESERVATION_HISTORY_INVALID")
                    if candidate == str(candidate_identity):
                        raise ExposureError("RESERVATION_CANDIDATE_DUPLICATE")
                    if account == str(account_identity):
                        total += Fraction(previous_amount)
                if total + Fraction(amount_eur) > Fraction(budget_eur):
                    return False
                db.execute(
                    "INSERT INTO exposure_reservations VALUES (?, ?, ?)",
                    (str(account_identity), str(candidate_identity), format(amount_eur, "f")),
                )
                db.commit()
                return True
        except sqlite3.Error:
            raise ExposureError("RESERVATION_STORE_ERROR") from None
