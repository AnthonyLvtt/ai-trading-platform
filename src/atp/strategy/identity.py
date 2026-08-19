from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from atp.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class _StrategyIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.strip() != self.value:
            raise ValidationError("Strategy identifier must be non-empty and trimmed")
        if not unicodedata.is_normalized("NFC", self.value):
            raise ValidationError("Strategy identifier must use NFC Unicode normalization")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StrategyId(_StrategyIdentifier):
    """Stable identity of a versioned strategy definition."""


@dataclass(frozen=True, slots=True)
class StrategyEvaluationId(_StrategyIdentifier):
    """Identity of one deterministic single-symbol evaluation."""


@dataclass(frozen=True, slots=True)
class StrategyDecisionId(_StrategyIdentifier):
    """Identity of one economic proposal, never an order identity."""
