from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from atp.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class _AccountingIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.strip() != self.value:
            raise ValidationError("Accounting identifier must be non-empty and trimmed")
        if not unicodedata.is_normalized("NFC", self.value):
            raise ValidationError("Accounting identifier must use NFC Unicode normalization")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class AccountingPolicyId(_AccountingIdentifier):
    pass


@dataclass(frozen=True, slots=True)
class AccountingEntryId(_AccountingIdentifier):
    pass


@dataclass(frozen=True, slots=True)
class AccountingReplayId(_AccountingIdentifier):
    pass


@dataclass(frozen=True, slots=True)
class AccountingValuationId(_AccountingIdentifier):
    pass
