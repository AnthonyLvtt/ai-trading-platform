from __future__ import annotations

from dataclasses import dataclass

from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class LineageStep:
    operation: str
    version: str
    input_identities: tuple[ContentIdentity, ...] = ()

    def __post_init__(self) -> None:
        if not self.operation or self.operation.strip() != self.operation:
            raise ValidationError("lineage operation must be non-empty and trimmed")
        if not self.version or self.version.strip() != self.version:
            raise ValidationError("lineage version must be non-empty and trimmed")

    def canonical_value(self) -> dict[str, object]:
        return {
            "input_identities": [str(identity) for identity in self.input_identities],
            "operation": self.operation,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class DataLineage:
    steps: tuple[LineageStep, ...]

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValidationError("DATA lineage must contain at least one step")

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical([step.canonical_value() for step in self.steps])
