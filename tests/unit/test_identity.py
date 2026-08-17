from __future__ import annotations

from atp.shared.identity import ContentIdentity


def test_content_identity_is_deterministic() -> None:
    assert ContentIdentity.from_text("same") == ContentIdentity.from_text("same")


def test_content_identity_changes_with_content() -> None:
    assert ContentIdentity.from_text("one") != ContentIdentity.from_text("two")
