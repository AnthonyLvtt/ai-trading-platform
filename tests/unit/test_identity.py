from __future__ import annotations

import pytest

from atp.shared.errors import ValidationError
from atp.shared.identity import CausationId, ContentIdentity, CorrelationId, EventId
from atp.shared.serialization import canonical_json_bytes


def test_typed_identifiers_are_equal_with_same_type_and_value() -> None:
    assert EventId("event-1") == EventId("event-1")


def test_typed_identifiers_remain_distinct_across_concepts() -> None:
    assert EventId("shared-value") != CorrelationId("shared-value")
    assert CorrelationId("shared-value") != CausationId("shared-value")


@pytest.mark.parametrize("value", ["", " event-1", "event-1 ", "e\u0301vent"])
def test_typed_identifier_rejects_empty_or_non_normalized_value(value: str) -> None:
    with pytest.raises(ValidationError):
        EventId(value)


def test_content_identity_is_deterministic() -> None:
    assert ContentIdentity.from_text("same") == ContentIdentity.from_text("same")


def test_content_identity_changes_with_content() -> None:
    assert ContentIdentity.from_text("one") != ContentIdentity.from_text("two")


def test_content_identity_identifies_exact_canonical_bytes() -> None:
    value = {"symbol": "BTC/EUR", "sequence": 7}

    identity = ContentIdentity.from_canonical(value)

    assert identity.algorithm == "sha256"
    assert identity == ContentIdentity.from_bytes(canonical_json_bytes(value))


def test_canonical_key_order_does_not_change_content_identity() -> None:
    left = {"symbol": "BTC/EUR", "sequence": 7}
    right = {"sequence": 7, "symbol": "BTC/EUR"}

    assert ContentIdentity.from_canonical(left) == ContentIdentity.from_canonical(right)
