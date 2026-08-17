from __future__ import annotations

import pytest

from atp.shared.environment import Environment, require_foundation_environment
from atp.shared.errors import ConfigurationError


def test_valid_environment_accepted() -> None:
    assert require_foundation_environment(Environment.LOCAL) is Environment.LOCAL


def test_unknown_environment_refused() -> None:
    with pytest.raises(ConfigurationError):
        Environment.parse("PRODUCTION")


def test_live_environment_not_enabled() -> None:
    with pytest.raises(ConfigurationError):
        require_foundation_environment(Environment.LIVE)
