from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import cast

from atp.data.identity import DatasetId, SnapshotId, SourceId
from atp.data.lineage import DataLineage
from atp.data.temporal import TemporalMetadata
from atp.shared.environment import Environment
from atp.shared.errors import DomainError, ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.shared.time import require_utc


class DataQuality(StrEnum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class GapStatus(StrEnum):
    NO_GAP_DETECTED = "NO_GAP_DETECTED"
    KNOWN_GAP = "KNOWN_GAP"
    GAP_STATUS_UNKNOWN = "GAP_STATUS_UNKNOWN"


class DataFinality(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    FINAL = "FINAL"


@dataclass(frozen=True, slots=True)
class Gap:
    start: datetime
    end: datetime
    reason: str

    def __post_init__(self) -> None:
        require_utc(self.start)
        require_utc(self.end)
        if self.end <= self.start:
            raise ValidationError("gap end must be after gap start")
        if not self.reason or self.reason.strip() != self.reason:
            raise ValidationError("gap reason must be non-empty and trimmed")

    def canonical_value(self) -> dict[str, str]:
        return {"end": self.end.isoformat(), "reason": self.reason, "start": self.start.isoformat()}


@dataclass(frozen=True, slots=True)
class DataPoint:
    symbol: str
    canonical_payload: bytes
    temporal: TemporalMetadata
    finality: DataFinality

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValidationError("symbol must be non-empty and trimmed")
        try:
            decoded = self.canonical_payload.decode("utf-8")
            parsed = cast(object, json.loads(decoded))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("canonical payload must be canonical UTF-8 JSON") from exc
        if canonical_json_bytes(parsed) != self.canonical_payload:
            raise ValidationError("payload bytes must use ATP canonical JSON encoding")

    @classmethod
    def from_value(
        cls,
        *,
        symbol: str,
        value: object,
        temporal: TemporalMetadata,
        finality: DataFinality,
    ) -> DataPoint:
        return cls(
            symbol=symbol,
            canonical_payload=canonical_json_bytes(value),
            temporal=temporal,
            finality=finality,
        )

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_bytes(self.canonical_payload)

    def canonical_value(self) -> dict[str, object]:
        rule = self.temporal.availability_rule
        return {
            "available_at": self.temporal.available_at.isoformat(),
            "availability_rule": None
            if rule is None
            else {"rule_id": rule.rule_id, "version": rule.version},
            "event_time": self.temporal.event_time.isoformat(),
            "finality": self.finality.value,
            "ingested_at": self.temporal.ingested_at.isoformat(),
            "payload_utf8": self.canonical_payload.decode("utf-8"),
            "provider_time": None
            if self.temporal.provider_time is None
            else self.temporal.provider_time.isoformat(),
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    dataset_id: DatasetId
    snapshot_id: SnapshotId
    content_identity: ContentIdentity
    source_id: SourceId
    environment: Environment
    schema_version: str
    transformation_version: str
    created_at: datetime
    points: tuple[DataPoint, ...]
    quality: DataQuality
    freshness: FreshnessStatus
    gap_status: GapStatus
    gaps: tuple[Gap, ...]
    degradation_reasons: frozenset[str]
    lineage: DataLineage
    validation_as_of_use: DataQuality
    current_validation_status: DataQuality

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        for field_name, value in (
            ("schema_version", self.schema_version),
            ("transformation_version", self.transformation_version),
        ):
            if not value or value.strip() != value:
                raise ValidationError(f"{field_name} must be non-empty and trimmed")
        if self.gap_status is GapStatus.KNOWN_GAP and not self.gaps:
            raise ValidationError("KNOWN_GAP requires at least one explicit gap")
        if self.gap_status is not GapStatus.KNOWN_GAP and self.gaps:
            raise ValidationError("explicit gaps require KNOWN_GAP status")
        if self.quality is DataQuality.DEGRADED and not self.degradation_reasons:
            raise ValidationError("DEGRADED quality requires explicit reasons")
        if self.quality is not DataQuality.DEGRADED and self.degradation_reasons:
            raise ValidationError("degradation reasons require DEGRADED quality")
        expected = ContentIdentity.from_canonical(self._content_value(self.points, self.gaps))
        if self.content_identity != expected:
            raise ValidationError("snapshot content_identity does not match canonical content")

    @classmethod
    def create(
        cls,
        *,
        dataset_id: DatasetId,
        snapshot_id: SnapshotId,
        source_id: SourceId,
        environment: Environment,
        schema_version: str,
        transformation_version: str,
        created_at: datetime,
        points: tuple[DataPoint, ...],
        quality: DataQuality,
        freshness: FreshnessStatus,
        gap_status: GapStatus,
        gaps: tuple[Gap, ...],
        degradation_reasons: frozenset[str],
        lineage: DataLineage,
    ) -> DatasetSnapshot:
        ordered_points = tuple(
            sorted(
                points,
                key=lambda point: (
                    point.temporal.event_time,
                    point.temporal.available_at,
                    point.symbol,
                    str(point.content_identity),
                ),
            )
        )
        ordered_gaps = tuple(sorted(gaps, key=lambda gap: (gap.start, gap.end, gap.reason)))
        identity = ContentIdentity.from_canonical(cls._content_value(ordered_points, ordered_gaps))
        return cls(
            dataset_id=dataset_id,
            snapshot_id=snapshot_id,
            content_identity=identity,
            source_id=source_id,
            environment=environment,
            schema_version=schema_version,
            transformation_version=transformation_version,
            created_at=created_at,
            points=ordered_points,
            quality=quality,
            freshness=freshness,
            gap_status=gap_status,
            gaps=ordered_gaps,
            degradation_reasons=degradation_reasons,
            lineage=lineage,
            validation_as_of_use=quality,
            current_validation_status=quality,
        )

    @staticmethod
    def _content_value(points: tuple[DataPoint, ...], gaps: tuple[Gap, ...]) -> dict[str, object]:
        return {
            "gaps": [gap.canonical_value() for gap in gaps],
            "points": [point.canonical_value() for point in points],
        }

    def with_current_validation(self, status: DataQuality) -> DatasetSnapshot:
        """Record new validation evidence without rewriting validation_as_of_use."""
        return replace(self, current_validation_status=status)


def assert_snapshot_consistent(
    existing: DatasetSnapshot, candidate: DatasetSnapshot
) -> DatasetSnapshot:
    if existing.snapshot_id != candidate.snapshot_id:
        return candidate
    if existing.content_identity != candidate.content_identity:
        raise DomainError("same snapshot_id has contradictory content_identity")
    immutable_manifest = (
        "dataset_id",
        "source_id",
        "environment",
        "schema_version",
        "transformation_version",
        "created_at",
        "lineage",
    )
    if any(getattr(existing, field) != getattr(candidate, field) for field in immutable_manifest):
        raise DomainError("same snapshot_id has contradictory immutable manifest")
    return candidate
