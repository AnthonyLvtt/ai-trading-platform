from __future__ import annotations

from dataclasses import dataclass

from atp.risk.identity import RiskPolicyId
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    policy_id: RiskPolicyId
    version: str
    allowed_environments: frozenset[Environment]
    max_positions: int

    @classmethod
    def v1(cls, *, policy_id: RiskPolicyId, version: str) -> RiskPolicy:
        return cls(
            policy_id=policy_id,
            version=version,
            allowed_environments=ACTIVE_ENVIRONMENTS,
            max_positions=1,
        )

    def __post_init__(self) -> None:
        if not self.version or self.version.strip() != self.version:
            raise ValidationError("Risk policy version must be non-empty and trimmed")
        if self.allowed_environments != ACTIVE_ENVIRONMENTS:
            raise ValidationError("Risk V1 environments must match the CTO-approved active set")
        if self.max_positions != 1:
            raise ValidationError("Risk V1 max_positions must be exactly 1")

    def canonical_value(self) -> dict[str, object]:
        return {
            "allowed_environments": sorted(
                environment.value for environment in self.allowed_environments
            ),
            "instrument_class": "SPOT",
            "leverage": "1",
            "long_only": True,
            "margin_enabled": False,
            "market_type": "SPOT",
            "max_positions": self.max_positions,
            "policy_id": str(self.policy_id),
            "version": self.version,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())
