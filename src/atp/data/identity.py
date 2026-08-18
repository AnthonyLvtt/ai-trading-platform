from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from atp.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class _DataIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.strip() != self.value:
            raise ValidationError("DATA identifier must be non-empty and trimmed")
        if not unicodedata.is_normalized("NFC", self.value):
            raise ValidationError("DATA identifier must use NFC Unicode normalization")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SourceId(_DataIdentifier):
    """Stable identity of a logical, non-secret data source."""


@dataclass(frozen=True, slots=True)
class DatasetId(_DataIdentifier):
    """Identity of a logical, versioned dataset."""


@dataclass(frozen=True, slots=True)
class SnapshotId(_DataIdentifier):
    """Identity of one frozen dataset materialization."""


@dataclass(frozen=True, slots=True)
class UniverseSnapshotId(_DataIdentifier):
    """Identity of one immutable historical universe."""
