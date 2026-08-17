from __future__ import annotations

import pytest

from atp.shared.config import AppConfig
from atp.shared.environment import Environment
from atp.shared.errors import ConfigurationError


def test_secret_absent_when_not_required() -> None:
    config = AppConfig.from_env({"ATP_ENV": "LOCAL"})
    assert config.environment is Environment.LOCAL


def test_environment_is_explicit_no_live_fallback() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_env({})


def test_invalid_configuration_refused() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_env({"ATP_ENV": "LOCAL", "ATP_LOG_LEVEL": "LOUD"})


def test_live_credentials_forbidden_in_standard_tests() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_env({"ATP_ENV": "TEST", "BINANCE_LIVE_API_KEY": "secret"})
