from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from atp.shared.environment import Environment
from atp.shared.identity import CausationId, CorrelationId, EventId
from atp.shared.time import require_utc


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Minimal normative event envelope for domain-produced facts.

    This is an observability transport contract only. It does not make
    Observability authoritative for the domain state represented by payload.
    """

    event_id: EventId
    event_type: str
    event_version: int
    occurred_at: datetime
    observed_at: datetime
    producer: str
    environment: Environment
    payload: Mapping[str, object]
    correlation_id: CorrelationId | None = None
    causation_id: CausationId | None = None

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type must be non-empty")
        if self.event_version < 1:
            raise ValueError("event_version must be >= 1")
        if not self.producer.strip():
            raise ValueError("producer must be non-empty")
        require_utc(self.occurred_at)
        require_utc(self.observed_at)
