from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Self

from atp.shared.errors import ValidationError
from atp.shared.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class _TypedIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value.strip() != self.value:
            raise ValidationError("identifier must be non-empty and trimmed")
        if not unicodedata.is_normalized("NFC", self.value):
            raise ValidationError("identifier must use NFC Unicode normalization")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CorrelationId(_TypedIdentifier):
    pass


@dataclass(frozen=True, slots=True)
class EventId(_TypedIdentifier):
    pass


@dataclass(frozen=True, slots=True)
class CausationId(_TypedIdentifier):
    pass


@dataclass(frozen=True, slots=True)
class ContentIdentity:
    algorithm: str
    digest: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise ValidationError(f"unsupported content identity algorithm: {self.algorithm!r}")
        if len(self.digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.digest
        ):
            raise ValidationError("content identity digest must be lowercase sha256 hex")

    @classmethod
    def from_bytes(cls, content: bytes) -> Self:
        return cls(algorithm="sha256", digest=hashlib.sha256(content).hexdigest())

    @classmethod
    def from_text(cls, content: str) -> Self:
        return cls.from_bytes(content.encode("utf-8"))

    @classmethod
    def from_canonical(cls, value: object) -> Self:
        return cls.from_bytes(canonical_json_bytes(value))

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.digest}"
