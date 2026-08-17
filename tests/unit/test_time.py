from __future__ import annotations

from datetime import UTC

from atp.shared.time import utc_now


def test_timestamp_is_utc() -> None:
    timestamp = utc_now()
    assert timestamp.tzinfo is UTC
    assert timestamp.utcoffset() == UTC.utcoffset(timestamp)
