"""Read-only Exchange evidence contracts for Kraken. No execution authority exists here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from atp.observability.events import SENSITIVE_KEYS
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity


class EvidenceError(ValueError):
    pass


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is Decimal:
        if not value.is_finite():
            raise ValidationError("invalid decimal")
        return _decimal_text(value)
    if type(value) is datetime:
        if value.tzinfo is None:
            raise ValidationError("timezone required")
        return value.astimezone(UTC).isoformat()
    if type(value) is ContentIdentity:
        value.__post_init__()
        return str(value)
    if type(value) is tuple:
        return [canonical(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: canonical(getattr(value, f.name))
            for f in fields(value)
            if f.name != "content_identity"
        }
    raise ValidationError("invalid exchange evidence")


def typed(value: object, expected: object, depth: int = 0) -> bool:
    if depth > 25:
        return False
    if get_origin(expected) is UnionType:
        return any(typed(value, t, depth + 1) for t in get_args(expected))
    if get_origin(expected) is tuple:
        args = get_args(expected)
        return (
            type(value) is tuple
            and len(args) == 2
            and args[1] is Ellipsis
            and all(typed(v, args[0], depth + 1) for v in value)
        )
    if type(value) is not expected:
        return False
    if type(value) is Decimal:
        return value.is_finite()
    if type(value) is datetime:
        return value.tzinfo is not None
    if is_dataclass(value) and not isinstance(value, type):
        hints = get_type_hints(type(value))
        return all(typed(getattr(value, f.name), hints[f.name], depth + 1) for f in fields(value))
    return True


def safe_json(value: object, depth: int = 0) -> None:
    if depth > 25:
        raise EvidenceError("INVALID_INPUT")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key.casefold() in SENSITIVE_KEYS:
                raise EvidenceError("CREDENTIAL_MATERIAL_DETECTED")
            safe_json(item, depth + 1)
    elif type(value) is list:
        for item in value:
            safe_json(item, depth + 1)
    elif type(value) is str:
        if re.search(
            r"(?i)(?:" + "|".join(re.escape(k) for k in SENSITIVE_KEYS) + r')[\s"\x27]*[:=]',
            value,
        ):
            raise EvidenceError("CREDENTIAL_MATERIAL_DETECTED")
    elif value is not None and type(value) not in (bool, int):
        raise EvidenceError("INVALID_INPUT")


def encoded(value: object) -> object:
    if type(value) is tuple:
        return [encoded(v) for v in value]
    if type(value) is bytes:
        data = json.loads(value)
        safe_json(data)
        return data
    if is_dataclass(value) and not isinstance(value, type) and type(value) is not ContentIdentity:
        return {
            f.name: encoded(getattr(value, f.name))
            for f in fields(value)
            if f.name != "content_identity"
        }
    return canonical(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRecord:
    content_identity: ContentIdentity = field(init=False)

    def __post_init__(self) -> None:
        for flag in ("submission_authorized", "side_effect_performed", "safe_to_retry"):
            if hasattr(self, flag) and getattr(self, flag) is not False:
                raise EvidenceError("SIDE_EFFECT_NOT_AUTHORIZED")
        value = encoded(self)
        safe_json(value)
        digest = ContentIdentity.from_canonical(value)
        if hasattr(self, "content_identity") and self.content_identity != digest:
            raise EvidenceError("IDENTITY_MISMATCH")
        object.__setattr__(self, "content_identity", digest)


def verify_record(value: object, expected: type[object]) -> bool:
    try:
        if not typed(value, expected):
            return False

        def visit(item: object) -> None:
            if type(item) is tuple:
                for child in item:
                    visit(child)
            elif is_dataclass(item) and not isinstance(item, type):
                for f in fields(item):
                    visit(getattr(item, f.name))
                post = getattr(item, "__post_init__", None)
                if post is not None:
                    post()

        visit(value)
        safe_json(encoded(value))
        return True
    except (AttributeError, TypeError, ValueError, ValidationError, RecursionError):
        return False
