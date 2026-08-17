from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
}


def _redact(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, Mapping):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_redact(item, secrets) for item in value)
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, set):
        return {_redact(item, secrets) for item in value}
    return value


class SecretRedactionFilter(logging.Filter):
    """Redact configured secret values before a record reaches a handler."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg, self.secrets)
        record.args = _redact(record.args, self.secrets)
        for key, value in tuple(record.__dict__.items()):
            if key not in _RESERVED:
                setattr(record, key, _redact(value, self.secrets))
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
            payload["exception"] = self.formatException(record.exc_info)
        sanitized = _redact(payload, self._secrets)
        return json.dumps(sanitized, sort_keys=True, default=str)


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
