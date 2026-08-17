from __future__ import annotations

from enum import StrEnum

from atp.shared.errors import ConfigurationError


class Environment(StrEnum):
    LOCAL = "LOCAL"
    TEST = "TEST"
    BACKTEST = "BACKTEST"
    SIMULATION = "SIMULATION"
    DRY_RUN = "DRY_RUN"
    TESTNET = "TESTNET"
    LIVE = "LIVE"

    @classmethod
    def parse(cls, raw: str) -> Environment:
        try:
            return cls(raw.strip().upper())
        except ValueError as exc:
            raise ConfigurationError(f"unknown environment: {raw!r}") from exc


ACTIVE_FOUNDATION_ENVIRONMENTS = frozenset(
    {Environment.LOCAL, Environment.TEST, Environment.BACKTEST, Environment.SIMULATION}
)


def require_foundation_environment(environment: Environment) -> Environment:
    if environment not in ACTIVE_FOUNDATION_ENVIRONMENTS:
        raise ConfigurationError(f"environment {environment.value} is not enabled by ENG-FOUND-001")
    return environment
