from __future__ import annotations

import io
import logging

from atp.observability.logging import JsonFormatter, SecretRedactionFilter


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
