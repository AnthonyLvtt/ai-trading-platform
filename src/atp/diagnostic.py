from __future__ import annotations

from atp.observability.logging import configure_structured_logging
from atp.shared.config import AppConfig


def run_diagnostic(config: AppConfig) -> None:
    logger = configure_structured_logging(level=config.log_level)
    logger.info(
        "ATP foundation diagnostic ready",
        extra={"environment": config.environment.value, "exchange_contacted": False},
    )


def main() -> None:
    run_diagnostic(AppConfig.from_env())


if __name__ == "__main__":
    main()
