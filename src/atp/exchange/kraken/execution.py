"""Kraken economic-route description with execution disabled by construction."""

from __future__ import annotations

from enum import StrEnum

from atp.exchange.execution import EconomicExecutionPort, ExecutionError, OrderIntent


class KrakenEconomicRoute(StrEnum):
    ADD_ORDER = "/0/private/AddOrder"


class DisabledKrakenEconomicTransport(EconomicExecutionPort):
    """No socket, HTTP, credential, signing, retry, or redirect surface exists."""

    route = KrakenEconomicRoute.ADD_ORDER

    def submit(self, intent: OrderIntent) -> None:
        if type(intent) is not OrderIntent:
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        raise ExecutionError("KRAKEN_ECONOMIC_EXECUTION_NOT_QUALIFIED")
