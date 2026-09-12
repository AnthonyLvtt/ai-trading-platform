import importlib
import subprocess
import sys
from pathlib import Path

from atp.release_deployment.source import inspect_source


def test_canonical_producer_blocks_present_but_stale_lock(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    producer = importlib.import_module("release")
    monkeypatch.setenv("UV_OFFLINE", "true")
    root = tmp_path / "source"
    root.mkdir()
    project = root / "pyproject.toml"
    project.write_text(
        '[project]\nname = "release-fixture"\nversion = "1.0"\n'
        'requires-python = ">=3.12,<3.13"\ndependencies = []\n'
    )
    subprocess.run(
        ["uv", "lock", "--python", sys.executable], cwd=root, capture_output=True, check=True
    )
    lock_bytes = (root / "uv.lock").read_bytes()
    # Commit inconsistent metadata and lock together: byte identity and cleanliness
    # alone cannot prove that the dependency lock describes the current project.
    project.write_text(project.read_text().replace('version = "1.0"', 'version = "2.0"'))
    for args in [
        ("init", "-b", "main"),
        ("config", "user.name", "Release fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("add", "pyproject.toml", "uv.lock"),
        ("commit", "-m", "stale lock fixture"),
    ]:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    source = inspect_source(root)
    assert source.clean and source.lockfile_identity is not None
    run = subprocess.run
    checks = []

    def guarded_run(command, **kwargs):
        if command[:3] == ["uv", "lock", "--check"]:
            checks.append(command)
            return run(command, **kwargs)
        assert command[0] == "git", "stale lock must stop validation/build"
        return run(command, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("stale lock must stop qualification/evidence/candidate creation")

    monkeypatch.setattr(producer.subprocess, "run", guarded_run)
    monkeypatch.setattr(producer, "collect", forbidden)
    monkeypatch.setattr(producer, "ValidationEvidence", forbidden)
    monkeypatch.setattr(producer, "create_candidate", forbidden)
    output = tmp_path / "release-output"
    for _ in range(2):
        assert producer.run(root, "fixture", output, source.source_commit_sha) == 1
        assert capsys.readouterr().out == "BLOCKED LOCKFILE_MISMATCH\n"
    assert checks == [["uv", "lock", "--check"]] * 2
    assert not output.exists()
    assert (root / "uv.lock").read_bytes() == lock_bytes
    assert inspect_source(root) == source
