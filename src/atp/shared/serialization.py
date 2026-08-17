from __future__ import annotations

import json
import math
from collections.abc import Mapping

from atp.shared.errors import ValidationError


def canonical_json_bytes(value: object) -> bytes:
    """Encode a minimal JSON-compatible value deterministically as UTF-8 bytes."""
    normalized = _normalize_canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _normalize_canonical_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("canonical JSON does not support non-finite numbers")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("canonical JSON mapping keys must be strings")
            normalized[key] = _normalize_canonical_value(item)
        return normalized
    if isinstance(value, list | tuple):
        return [_normalize_canonical_value(item) for item in value]
    raise ValidationError(f"unsupported canonical JSON value: {type(value).__name__}")
