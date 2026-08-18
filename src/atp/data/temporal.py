from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp.shared.errors import ValidationError
from atp.shared.time import require_utc


@dataclass(frozen=True, slots=True)
class AvailabilityRule:
    """Versioned deterministic rule for deriving historical availability."""

    rule_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.rule_id or self.rule_id.strip() != self.rule_id:
            raise ValidationError("availability rule_id must be non-empty and trimmed")
        if not self.version or self.version.strip() != self.version:
            raise ValidationError("availability rule version must be non-empty and trimmed")

    def derive(self, *, event_time: datetime, ingested_at: datetime) -> datetime:
        """Information is available no earlier than both occurrence and ATP ingestion."""
        require_utc(event_time)
        require_utc(ingested_at)
        return max(event_time, ingested_at)


@dataclass(frozen=True, slots=True)
class TemporalMetadata:
    event_time: datetime
    provider_time: datetime | None
    ingested_at: datetime
    available_at: datetime
    availability_rule: AvailabilityRule | None = None

    def __post_init__(self) -> None:
        require_utc(self.event_time)
        if self.provider_time is not None:
            require_utc(self.provider_time)
        require_utc(self.ingested_at)
        require_utc(self.available_at)
        if self.available_at < self.event_time or self.available_at < self.ingested_at:
            raise ValidationError("available_at cannot precede event_time or ingested_at")

    @classmethod
    def derived(
        cls,
        *,
        event_time: datetime,
        provider_time: datetime | None,
        ingested_at: datetime,
        rule: AvailabilityRule,
    ) -> TemporalMetadata:
        return cls(
            event_time=event_time,
            provider_time=provider_time,
            ingested_at=ingested_at,
            available_at=rule.derive(event_time=event_time, ingested_at=ingested_at),
            availability_rule=rule,
        )

    def is_available_at(self, instant: datetime) -> bool:
        require_utc(instant)
        return self.available_at <= instant
