from __future__ import annotations

import io
import logging

from atp.observability.logging import JsonFormatter, SecretRedactionFilter


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
    assert "[REDACTED]" in output


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
