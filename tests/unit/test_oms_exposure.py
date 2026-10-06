from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from atp.exchange.contracts import VenueId
from atp.oms.exposure import (
    BoundedFeeEvidence,
    ExposureError,
    ExposureReservationLedger,
    FreshnessEvidence,
    SpendableEurEvidence,
)
from atp.shared.identity import ContentIdentity


def identity(value: str) -> ContentIdentity:
    return ContentIdentity.from_text(value)


AT = datetime(2026, 10, 6, tzinfo=UTC)


def test_evidence_is_bound_and_cannot_claim_a_side_effect() -> None:
    account = identity("account")
    spendable = SpendableEurEvidence(
        VenueId.KRAKEN,
        account,
        identity("credential"),
        identity("balance"),
        Decimal("100"),
        AT,
        identity("qualification"),
    )
    fee = BoundedFeeEvidence(
        VenueId.KRAKEN,
        account,
        identity("candidate"),
        Decimal("2"),
        AT,
        identity("fee-source"),
        identity("fee-qualification"),
    )
    freshness = FreshnessEvidence(
        spendable.content_identity,
        AT,
        AT + timedelta(seconds=5),
        Decimal("5"),
        identity("freshness-policy"),
    )
    assert spendable.content_identity != fee.content_identity
    assert freshness.subject_identity == spendable.content_identity
    with pytest.raises(ExposureError, match="SPENDABLE_EUR_EVIDENCE_INVALID"):
        replace(spendable, amount_eur=Decimal("NaN"))
    with pytest.raises(ExposureError, match="BOUNDED_FEE_EVIDENCE_INVALID"):
        replace(fee, maximum_fee_eur=Decimal("-1"))
    with pytest.raises(ExposureError, match="FRESHNESS_EVIDENCE_INVALID"):
        replace(freshness, assessed_at=AT + timedelta(seconds=6))
    with pytest.raises((ExposureError, ValueError)):
        replace(spendable, side_effect_performed=True)


def test_atomic_reservation_blocks_competing_candidates(tmp_path: Path) -> None:
    path = tmp_path / "reservations.sqlite"
    account = identity("same-account")

    def reserve(index: int) -> bool:
        return ExposureReservationLedger(path).reserve(
            account_identity=account,
            candidate_identity=identity(f"candidate-{index}"),
            budget_eur=Decimal("100"),
            amount_eur=Decimal("60"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, (1, 2))) == [False, True]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM exposure_reservations").fetchone() == (1,)


def test_reservations_persist_across_snapshots_and_duplicate_blocks(tmp_path: Path) -> None:
    ledger = ExposureReservationLedger(tmp_path / "reservations.sqlite")
    account = identity("account")
    first = identity("first")
    assert ledger.reserve(
        account_identity=account,
        candidate_identity=first,
        budget_eur=Decimal("1.00"),
        amount_eur=Decimal("0.60"),
    )
    assert ledger.reserve(
        account_identity=account,
        candidate_identity=identity("second"),
        budget_eur=Decimal("1.00"),
        amount_eur=Decimal("0.40"),
    )
    assert not ledger.reserve(
        account_identity=account,
        candidate_identity=identity("third"),
        budget_eur=Decimal("1.00"),
        amount_eur=Decimal("0.01"),
    )
    with pytest.raises(ExposureError, match="RESERVATION_CANDIDATE_DUPLICATE"):
        ledger.reserve(
            account_identity=account,
            candidate_identity=first,
            budget_eur=Decimal("100"),
            amount_eur=Decimal("1"),
        )


def test_corrupt_reservation_history_blocks(tmp_path: Path) -> None:
    path = tmp_path / "reservations.sqlite"
    ledger = ExposureReservationLedger(path)
    account = identity("account")
    assert ledger.reserve(
        account_identity=account,
        candidate_identity=identity("first"),
        budget_eur=Decimal("10"),
        amount_eur=Decimal("1"),
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE exposure_reservations SET amount_eur = 'NaN'")
    with pytest.raises(ExposureError, match="RESERVATION_HISTORY_INVALID"):
        ledger.reserve(
            account_identity=account,
            candidate_identity=identity("second"),
            budget_eur=Decimal("10"),
            amount_eur=Decimal("1"),
        )
