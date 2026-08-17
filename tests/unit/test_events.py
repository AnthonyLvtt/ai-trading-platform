from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atp.observability.events import DomainEvent
from atp.shared.environment import Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import EventId


def test_domain_event_preserves_occurred_and_observed_time() -> None:
    occurred_at = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    observed_at = datetime(2026, 8, 18, 0, 0, 2, tzinfo=UTC)
    event = DomainEvent(
        event_id=EventId("evt-1"),
        event_type="foundation.ready",
        event_version=1,
        occurred_at=occurred_at,
        observed_at=observed_at,
        producer="foundation",
        environment=Environment.LOCAL,
        payload={},
    )
    assert event.occurred_at == occurred_at
    assert event.observed_at == observed_at
    assert event.environment is Environment.LOCAL


def test_domain_event_rejects_non_utc_time() -> None:
    with pytest.raises(ValidationError):
        DomainEvent(
            event_id=EventId("evt-1"),
            event_type="foundation.ready",
            event_version=1,
            occurred_at=datetime(2026, 8, 18, 0, 0),
            observed_at=datetime(2026, 8, 18, 0, 0, tzinfo=UTC),
            producer="foundation",
            environment=Environment.LOCAL,
            payload={},
        )
