from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp.data.identity import SnapshotId, UniverseSnapshotId
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import require_utc


@dataclass(frozen=True, slots=True)
class SymbolDecision:
    symbol: str
    eligible: bool
    reason: str
    evidence_available_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.evidence_available_at)
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValidationError("universe symbol must be non-empty and trimmed")
        if not self.reason or self.reason.strip() != self.reason:
            raise ValidationError("universe decision reason must be non-empty and trimmed")

    def canonical_value(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "evidence_available_at": self.evidence_available_at.isoformat(),
            "reason": self.reason,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    universe_snapshot_id: UniverseSnapshotId
    created_at: datetime
    effective_at: datetime
    rules_version: str
    source_snapshot_ids: tuple[SnapshotId, ...]
    decisions: tuple[SymbolDecision, ...]
    content_identity: ContentIdentity

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        require_utc(self.effective_at)
        if not self.rules_version or self.rules_version.strip() != self.rules_version:
            raise ValidationError("universe rules_version must be non-empty and trimmed")
        symbols = [decision.symbol for decision in self.decisions]
        if len(symbols) != len(set(symbols)):
            raise ValidationError("universe decisions must contain unique symbols")
        if any(decision.evidence_available_at > self.effective_at for decision in self.decisions):
            raise ValidationError(
                "universe decision cannot use evidence unavailable at effective_at"
            )
        expected = ContentIdentity.from_canonical(self._content_value(self.decisions))
        if self.content_identity != expected:
            raise ValidationError("universe content_identity does not match decisions")

    @classmethod
    def create(
        cls,
        *,
        universe_snapshot_id: UniverseSnapshotId,
        created_at: datetime,
        effective_at: datetime,
        rules_version: str,
        source_snapshot_ids: tuple[SnapshotId, ...],
        decisions: tuple[SymbolDecision, ...],
    ) -> UniverseSnapshot:
        ordered = tuple(sorted(decisions, key=lambda decision: decision.symbol))
        return cls(
            universe_snapshot_id=universe_snapshot_id,
            created_at=created_at,
            effective_at=effective_at,
            rules_version=rules_version,
            source_snapshot_ids=tuple(sorted(source_snapshot_ids, key=str)),
            decisions=ordered,
            content_identity=ContentIdentity.from_canonical(cls._content_value(ordered)),
        )

    @staticmethod
    def _content_value(decisions: tuple[SymbolDecision, ...]) -> list[dict[str, object]]:
        return [decision.canonical_value() for decision in decisions]

    @property
    def eligible_symbols(self) -> frozenset[str]:
        return frozenset(decision.symbol for decision in self.decisions if decision.eligible)
