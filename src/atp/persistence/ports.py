from __future__ import annotations

from typing import Protocol, TypeVar

T = TypeVar("T")


class Repository(Protocol[T]):
    """Minimal persistence port; transaction semantics belong to concrete domain contracts."""

    def get(self, identity: str) -> T | None: ...

    def put(self, identity: str, value: T) -> None: ...
