from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from atp.accounting.identity import AccountingPolicyId
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class AccountingPolicy:
    policy_id: AccountingPolicyId = AccountingPolicyId("ATP_ACCOUNTING_V1")
    version: str = "1.0"
    currency: str = "USDT"

    def canonical_value(self) -> dict[str, object]:
        return {
            "arithmetic": "DECIMAL",
            "business_rounding": None,
            "cost_basis": "SINGLE_ENTRY_PRICE",
            "currency": self.currency,
            "direction": "LONG_ONLY",
            "fees": str(Decimal("0")),
            "market": "SPOT",
            "mark_field": "CLOSE",
            "max_positions": 1,
            "partial_entries": False,
            "partial_exits": False,
            "policy_id": str(self.policy_id),
            "quantity_source": "EXTERNAL_FACT",
            "version": self.version,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


ACCOUNTING_POLICY_V1 = AccountingPolicy()
