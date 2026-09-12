"""Explicit local Git boundary; checks tracked bytes and rejects untracked source changes."""

from __future__ import annotations

import subprocess
from pathlib import Path

from atp.release_deployment.model import Reason, ReleaseError, SourceFile, SourceTree, encode
from atp.shared.identity import ContentIdentity


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
    return result.stdout


def inspect_source(root: Path) -> SourceTree:
    if type(root) is not type(Path()) or not root.is_dir():
        raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
    try:
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        branch = _git(root, "branch", "--show-current").decode().strip()
        tree = _git(root, "rev-parse", "HEAD^{tree}").decode().strip()
        entries = _git(root, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0")
        files = []
        for entry in entries:
            if not entry:
                continue
            meta, name = entry.split(b"\t", 1)
            mode, kind, _ = meta.decode().split()
            path = name.decode("utf-8")
            file = root / path
            if kind != "blob" or mode not in ("100644", "100755") or file.is_symlink():
                raise ReleaseError(Reason.INVALID_SOURCE_COMMIT)
            if not file.is_file():
                raise ReleaseError(Reason.DIRTY_WORKTREE)
            files.append(SourceFile(path, mode, ContentIdentity.from_bytes(file.read_bytes())))
        ordered = tuple(sorted(files, key=lambda f: f.path))
        repository_identity = ContentIdentity.from_canonical(encode(ordered))
        lock = next((f.digest for f in ordered if f.path == "uv.lock"), None)
        clean = not _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        return SourceTree(commit, branch, tree, repository_identity, lock, ordered, clean)
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError(Reason.INVALID_SOURCE_COMMIT) from None
