from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime

from atp.observability.logging import (
    DiagnosticLogRecord,
    JsonFormatter,
    SecretRedactionFilter,
)
from atp.shared.identity import CorrelationId, EventId


class _SecretBearingObject:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def __str__(self) -> str:
        return f"object-secret={self.secret}"


def _logger_with_redaction(stream: io.StringIO, secret: str) -> logging.Logger:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(secrets=[secret]))
    handler.addFilter(SecretRedactionFilter([secret]))

    logger = logging.getLogger("atp.test.redaction")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def test_logs_do_not_contain_injected_secret() -> None:
    secret = "super-secret-token"
    stream = io.StringIO()
    logger = _logger_with_redaction(stream, secret)

    logger.info(
        "credential=%s",
        secret,
        extra={"token": secret, "nested": {"items": [secret]}},
    )

    output = stream.getvalue()
    assert secret not in output
    assert "[REDACTED]" in output


def test_exception_text_is_redacted_before_output() -> None:
    secret = "super-secret-token"
    stream = io.StringIO()
    logger = _logger_with_redaction(stream, secret)

    try:
        raise RuntimeError(f"failed with credential {secret}")
    except RuntimeError:
        logger.exception("operation failed")

    output = stream.getvalue()
    assert secret not in output
    assert '"exception_type": "RuntimeError"' in output


def test_mapping_keys_are_redacted_before_output() -> None:
    secret = "super-secret-token"
    stream = io.StringIO()
    logger = _logger_with_redaction(stream, secret)

    logger.info(
        "mapping",
        extra={"payload": {_SecretBearingObject(secret): "safe"}},
    )

    output = stream.getvalue()
    assert secret not in output
    assert "object-secret=[REDACTED]" in output


def test_arbitrary_object_string_conversion_is_redacted() -> None:
    secret = "super-secret-token"
    stream = io.StringIO()
    logger = _logger_with_redaction(stream, secret)

    logger.info(
        "object=%s",
        _SecretBearingObject(secret),
        extra={"payload": _SecretBearingObject(secret)},
    )

    output = stream.getvalue()
    assert secret not in output
    assert output.count("[REDACTED]") >= 2


def test_sensitive_keys_are_redacted_without_configured_values() -> None:
    stream = io.StringIO()
    logger = _logger_with_redaction(stream, "")

    logger.info(
        "authorization=fixture-credential",
        extra={"api_key": "fixture-credential", "nested": {"token": "fixture-credential"}},
    )

    output = stream.getvalue()
    assert "fixture-credential" not in output
    assert output.count("[REDACTED]") >= 3


def test_exception_message_is_not_rendered_implicitly() -> None:
    stream = io.StringIO()
    logger = _logger_with_redaction(stream, "")

    try:
        raise RuntimeError("token=fixture-credential")
    except RuntimeError:
        logger.exception("safe operation failure")

    output = stream.getvalue()
    assert "fixture-credential" not in output
    assert "RuntimeError" in output


def test_diagnostic_record_is_separate_and_uses_explicit_time() -> None:
    timestamp = datetime(2026, 9, 7, 12, tzinfo=UTC)
    record = DiagnosticLogRecord(
        timestamp=timestamp,
        level="INFO",
        logger="atp.backtesting",
        message="replay completed",
        correlation_id=CorrelationId("correlation:fixture"),
        event_id=EventId("obs-event:fixture"),
        context={"status": "COMPLETED"},
    )

    payload = json.loads(record.to_json())
    assert payload["timestamp"] == timestamp.isoformat()
    assert payload["event_id"] == "obs-event:fixture"
    assert "content_identity" not in payload
