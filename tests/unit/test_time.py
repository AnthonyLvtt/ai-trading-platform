from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from atp.shared.errors import ValidationError
from atp.shared.time import FixedClock, LogicalTime, require_utc, utc_now


def test_timestamp_is_utc() -> None:
    timestamp = utc_now()
    assert timestamp.tzinfo is UTC
    assert timestamp.utcoffset() == UTC.utcoffset(timestamp)


def test_non_utc_timestamp_is_rejected() -> None:
    non_utc = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))

    with pytest.raises(ValidationError):
        require_utc(non_utc)

    with pytest.raises(ValidationError):
        LogicalTime(non_utc)


def test_fixed_clock_is_deterministic() -> None:
    instant = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    clock = FixedClock(instant)

    assert clock.now() == instant
    assert clock.now() == clock.now()
