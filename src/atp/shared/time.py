from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from atp.shared.errors import ValidationError


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValidationError("timestamp must be timezone-aware UTC")
    return value


@dataclass(frozen=True, slots=True)
class LogicalTime:
    """UTC timestamp primitive without distributed-clock or Lamport semantics."""

    value: datetime

    def __post_init__(self) -> None:
        require_utc(self.value)


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemUtcClock:
    def now(self) -> datetime:
        return utc_now()


@dataclass(frozen=True, slots=True)
class FixedClock:
    instant: datetime

    def __post_init__(self) -> None:
        require_utc(self.instant)

    def now(self) -> datetime:
        return self.instant
