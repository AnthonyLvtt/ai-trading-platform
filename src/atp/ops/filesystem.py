"""Explicit local read-only boundary. OPS does not implicitly create directories."""

from pathlib import Path
from typing import Protocol


class FilesystemBoundary(Protocol):
    def resolve(self, path: str) -> str: ...
    def is_directory(self, path: str) -> bool: ...
    def artifacts_available(self, path: str) -> bool: ...


class LocalFilesystem:
    def resolve(self, path: str) -> str:
        return str(Path(path).resolve())

    def is_directory(self, path: str) -> bool:
        return Path(path).is_dir()

    def artifacts_available(self, path: str) -> bool:
        return Path(path).is_dir()
