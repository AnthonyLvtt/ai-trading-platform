from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from atp.observability.events import SENSITIVE_KEYS
from atp.shared.errors import ValidationError
from atp.shared.identity import CorrelationId, EventId
from atp.shared.time import require_utc

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


def _redact(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            safe_key = _redact_text(_safe_string(key), secrets)
            redacted[safe_key] = (
                "[REDACTED]" if safe_key.casefold() in SENSITIVE_KEYS else _redact(item, secrets)
            )
        return redacted
    if isinstance(value, tuple | list | set | frozenset):
        return [_redact(item, secrets) for item in value]
    return _redact_text(_safe_string(value), secrets)


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED]")
    keys = "|".join(re.escape(key) for key in sorted(SENSITIVE_KEYS, key=len, reverse=True))
    redacted = re.sub(
        rf"(?i)\b({keys})\b\s*[:=]\s*([^\s,;]+)",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted


def _safe_string(value: object) -> str:
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - logging must fail closed for arbitrary objects
        return "[UNSERIALIZABLE]"


def _redact_fail_closed(value: object, secrets: tuple[str, ...]) -> object:
    try:
        return _redact(value, secrets)
    except Exception:  # noqa: BLE001 - diagnostics must not expose or propagate unsafe values
        return "[UNSERIALIZABLE]"


@dataclass(frozen=True, slots=True)
class DiagnosticLogRecord:
    """Non-authoritative diagnostic record with an explicit logical timestamp."""

    timestamp: datetime
    level: str
    logger: str
    message: str
    correlation_id: CorrelationId | None = None
    event_id: EventId | None = None
    context: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        require_utc(self.timestamp)
        for name, value in (
            ("level", self.level),
            ("logger", self.logger),
            ("message", self.message),
        ):
            if not isinstance(value, str) or not value or value.strip() != value:
                raise ValidationError(f"Diagnostic log {name} must be non-empty and trimmed")

    def to_json(self, *, secrets: Iterable[str] = ()) -> str:
        payload: dict[str, object] = {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "event_id": None if self.event_id is None else str(self.event_id),
            "context": {} if self.context is None else self.context,
        }
        sanitized = _redact_fail_closed(payload, tuple(secret for secret in secrets if secret))
        return json.dumps(sanitized, sort_keys=True)


class SecretRedactionFilter(logging.Filter):
    """Redact configured secret values before a record reaches a handler."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_fail_closed(record.msg, self.secrets)
        record.args = cast(
            tuple[object, ...] | Mapping[str, object] | None,
            _redact_fail_closed(record.args, self.secrets),
        )
        for key, value in tuple(record.__dict__.items()):
            if key not in _RESERVED:
                setattr(record, key, _redact_fail_closed(value, self.secrets))
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, *, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            exception_type = record.exc_info[0]
            payload["exception_type"] = (
                "Exception" if exception_type is None else exception_type.__name__
            )
        sanitized = _redact_fail_closed(payload, self._secrets)
        return json.dumps(sanitized, sort_keys=True)


def configure_structured_logging(
    *, level: str = "INFO", secrets: Iterable[str] = ()
) -> logging.Logger:
    secret_values = tuple(secret for secret in secrets if secret)
    logger = logging.getLogger("atp")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(secrets=secret_values))
    handler.addFilter(SecretRedactionFilter(secret_values))
    logger.addHandler(handler)
    return logger
