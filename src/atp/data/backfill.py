from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp.data.snapshot import DataPoint
from atp.shared.errors import ValidationError
from atp.shared.time import require_utc


@dataclass(frozen=True, slots=True)
class BackfillEvidence:
    point: DataPoint
    backfilled_at: datetime
    reason: str
    historical_available_at: datetime | None
    current_dataset_complete: bool = True

    def __post_init__(self) -> None:
        require_utc(self.backfilled_at)
        if self.historical_available_at is not None:
            require_utc(self.historical_available_at)
            if self.historical_available_at != self.point.temporal.available_at:
                raise ValidationError(
                    "historical availability must preserve the original point availability"
                )
        if not self.reason or self.reason.strip() != self.reason:
            raise ValidationError("backfill reason must be non-empty and trimmed")

    def was_historically_available_at(self, instant: datetime) -> bool:
        require_utc(instant)
        return self.historical_available_at is not None and self.historical_available_at <= instant
