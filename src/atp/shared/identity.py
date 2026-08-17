from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Self

from atp.shared.errors import ValidationError


@dataclass(frozen=True, slots=True)
class Identifier:
    value: str

    def __post_init__(self) -> None:
        if not self.value or self.value.strip() != self.value:
            raise ValidationError("identifier must be non-empty and trimmed")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CorrelationId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class EventId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class CausationId(Identifier):
    pass


@dataclass(frozen=True, slots=True)
class ContentIdentity:
    algorithm: str
    digest: str

    @classmethod
    def from_bytes(cls, content: bytes) -> Self:
        return cls(algorithm="sha256", digest=hashlib.sha256(content).hexdigest())

    @classmethod
    def from_text(cls, content: str) -> Self:
        return cls.from_bytes(content.encode("utf-8"))

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.digest}"
