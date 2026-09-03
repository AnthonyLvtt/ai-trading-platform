from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from atp.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class _BacktestingIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.strip() != self.value:
            raise ValidationError("Backtesting identifier must be non-empty and trimmed")
        if not unicodedata.is_normalized("NFC", self.value):
            raise ValidationError("Backtesting identifier must use NFC Unicode normalization")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SimulationPolicyId(_BacktestingIdentifier):
    """Stable identity of a deterministic simulated-execution policy."""


@dataclass(frozen=True, slots=True)
class SimulatedOrderId(_BacktestingIdentifier):
    """Identity of one simulated order authorization artefact."""


@dataclass(frozen=True, slots=True)
class SimulatedFillId(_BacktestingIdentifier):
    """Identity of one deterministic simulated fill."""


@dataclass(frozen=True, slots=True)
class BacktestRunId(_BacktestingIdentifier):
    """Identity of one deterministic backtest replay."""
