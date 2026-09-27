"""Pure composition of canonical Kraken DATA into the existing Strategy contract."""

from atp.kraken_strategy.composition import (
    KRAKEN_STRATEGY_INTERVAL,
    KRAKEN_STRATEGY_RULES_VERSION,
    KRAKEN_STRATEGY_SYMBOL,
    compose_kraken_universe,
    evaluate_kraken_strategy,
)

__all__ = [
    "KRAKEN_STRATEGY_INTERVAL",
    "KRAKEN_STRATEGY_RULES_VERSION",
    "KRAKEN_STRATEGY_SYMBOL",
    "compose_kraken_universe",
    "evaluate_kraken_strategy",
]
