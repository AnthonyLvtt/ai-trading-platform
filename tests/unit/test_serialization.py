from __future__ import annotations

import pytest

from atp.shared.errors import ValidationError
from atp.shared.serialization import canonical_json_bytes


def test_canonical_serialization_is_stable_across_mapping_order() -> None:
    left = {"b": [2, 1], "a": {"enabled": True}}
    right = {"a": {"enabled": True}, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)


def test_canonical_serialization_uses_deterministic_utf8() -> None:
    assert canonical_json_bytes({"label": "café"}) == b'{"label":"caf\xc3\xa9"}'


@pytest.mark.parametrize("value", [{1: "value"}, {"values": {1, 2}}, float("nan")])
def test_canonical_serialization_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(ValidationError):
        canonical_json_bytes(value)
