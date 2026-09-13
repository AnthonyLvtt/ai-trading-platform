"""Public read-only server-time evidence parsing; no network access."""

from dataclasses import dataclass
from datetime import UTC, datetime

from atp.exchange.read_only import EvidenceRecord, safe_json, timestamp
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class ExchangeTimeEvidence(EvidenceRecord):
    server_time: datetime
    local_observed_at: datetime
    source_identity: ContentIdentity


def parse_server_time_evidence(payload: object, observed_at: object) -> ExchangeTimeEvidence | None:
    try:
        safe_json(payload)
        if (
            type(payload) is not dict
            or set(payload) != {"serverTime"}
            or type(observed_at) is not datetime
            or observed_at.tzinfo is not UTC
        ):
            return None
        return ExchangeTimeEvidence(
            timestamp(payload["serverTime"]), observed_at, ContentIdentity.from_canonical(payload)
        )
    except (ValueError, TypeError):
        return None
