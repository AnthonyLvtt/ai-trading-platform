from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from atp.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class _RiskIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.strip() != self.value:
            raise ValidationError("Risk identifier must be non-empty and trimmed")
        if not unicodedata.is_normalized("NFC", self.value):
            raise ValidationError("Risk identifier must use NFC Unicode normalization")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RiskPolicyId(_RiskIdentifier):
    """Stable identity of a versioned Risk policy."""


@dataclass(frozen=True, slots=True)
class RiskDecisionId(_RiskIdentifier):
    """Identity of one deterministic economic Risk decision."""


@dataclass(frozen=True, slots=True)
class PositionId(_RiskIdentifier):
    """Identity of a minimal open-position fact supplied to Risk."""
