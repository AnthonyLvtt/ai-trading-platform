from __future__ import annotations

from dataclasses import dataclass

from atp.backtesting.identity import SimulationPolicyId
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class SimulationPolicy:
    policy_id: SimulationPolicyId
    version: str

    @classmethod
    def v1(cls) -> SimulationPolicy:
        return cls(
            policy_id=SimulationPolicyId("ATP_SIM_EXEC_V1"),
            version="1.0",
        )

    def __post_init__(self) -> None:
        if self.policy_id != SimulationPolicyId("ATP_SIM_EXEC_V1"):
            raise ValidationError("Simulation V1 policy_id must be ATP_SIM_EXEC_V1")
        if self.version != "1.0":
            raise ValidationError("Simulation V1 policy version must be 1.0")

    def canonical_value(self) -> dict[str, object]:
        return {
            "direction": "LONG_ONLY",
            "execution_delay": "NEXT_BAR",
            "fees": None,
            "fill_field": "OPEN",
            "market": "SPOT",
            "max_positions": 1,
            "partial_fills": False,
            "policy_id": str(self.policy_id),
            "quantity_model": None,
            "slippage": None,
            "version": self.version,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())
