from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from atp.shared.environment import Environment, require_foundation_environment
from atp.shared.errors import ConfigurationError

LIVE_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "BINANCE_LIVE_API_KEY",
        "BINANCE_LIVE_API_SECRET",
        "ATP_LIVE_API_KEY",
        "ATP_LIVE_API_SECRET",
    }
)


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: Environment
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppConfig":
        values = os.environ if environ is None else environ
        raw_environment = values.get("ATP_ENV")
        if not raw_environment:
            raise ConfigurationError("ATP_ENV is required; no environment fallback is allowed")

        environment = require_foundation_environment(Environment.parse(raw_environment))
        log_level = values.get("ATP_LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(f"invalid ATP_LOG_LEVEL: {log_level!r}")

        if environment is Environment.TEST:
            forbidden = sorted(key for key in LIVE_CREDENTIAL_ENV_KEYS if values.get(key))
            if forbidden:
                raise ConfigurationError(
                    "Live credentials are forbidden in standard tests: " + ", ".join(forbidden)
                )

        return cls(environment=environment, log_level=log_level)
