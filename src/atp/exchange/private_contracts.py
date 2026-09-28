"""Exchange-neutral private read-only evidence.

These contracts describe account observations.  They grant no order, funding, or
runtime authority and never contain credential material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from atp.exchange.contracts import CanonicalInstrumentId, VenueId
from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.identity import ContentIdentity


class CanonicalAssetId(StrEnum):
    BTC = "BTC"
    EUR = "EUR"


@dataclass(frozen=True, slots=True)
class PrivateCredentialReference(EvidenceRecord):
    venue: VenueId
    reference_id: str
    environment: Environment
    provider_type: str = "LOCAL_OPAQUE_REFERENCE"

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or self.environment not in ACTIVE_ENVIRONMENTS
            or not re.fullmatch(r"[0-9a-f]{32}", self.reference_id)
            or self.provider_type != "LOCAL_OPAQUE_REFERENCE"
        ):
            raise EvidenceError("INVALID_PRIVATE_CREDENTIAL_REFERENCE")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class PrivateCredentialCapabilityEvidence(EvidenceRecord):
    venue: VenueId
    credential_reference_identity: ContentIdentity
    permissions: tuple[str, ...]
    funds_query: bool
    open_orders_query: bool
    trading_capability_absent: bool
    withdrawal_capability_absent: bool
    observed_at: datetime
    source_identity: ContentIdentity
    submission_authorized: bool = False
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if (
            self.venue is not VenueId.KRAKEN
            or type(self.credential_reference_identity) is not ContentIdentity
            or self.permissions != ("query-funds", "query-open-trades")
            or self.funds_query is not True
            or self.open_orders_query is not True
            or self.trading_capability_absent is not True
            or self.withdrawal_capability_absent is not True
            or self.observed_at.tzinfo is None
            or type(self.source_identity) is not ContentIdentity
        ):
            raise EvidenceError("INVALID_PRIVATE_CREDENTIAL_CAPABILITY")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class CanonicalBalance(EvidenceRecord):
    asset: CanonicalAssetId
    amount: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.asset) is not CanonicalAssetId
            or not self.amount.is_finite()
            or self.amount < 0
        ):
            raise EvidenceError("INVALID_CANONICAL_BALANCE")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class AccountBalanceEvidence(EvidenceRecord):
    venue: VenueId
    credential_reference_identity: ContentIdentity
    capability_identity: ContentIdentity
    balances: tuple[CanonicalBalance, ...]
    scope: str
    observed_at: datetime
    source_identity: ContentIdentity
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        assets = tuple(balance.asset for balance in self.balances)
        if (
            self.venue is not VenueId.KRAKEN
            or type(self.credential_reference_identity) is not ContentIdentity
            or type(self.capability_identity) is not ContentIdentity
            or assets != tuple(sorted(assets, key=lambda item: item.value))
            or len(set(assets)) != len(assets)
            or self.scope != "ACCOUNT_WIDE_DEFAULT_WALLET"
            or self.observed_at.tzinfo is None
            or type(self.source_identity) is not ContentIdentity
        ):
            raise EvidenceError("INVALID_ACCOUNT_BALANCE_EVIDENCE")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class CanonicalOpenOrder(EvidenceRecord):
    exchange_order_id: str
    instrument: CanonicalInstrumentId
    side: str
    order_type: str
    quantity: Decimal
    executed_quantity: Decimal
    opened_at: datetime

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[A-Z0-9-]{1,40}", self.exchange_order_id)
            or self.side not in {"BUY", "SELL"}
            or self.order_type not in {"LIMIT", "MARKET"}
            or not self.quantity.is_finite()
            or self.quantity <= 0
            or not self.executed_quantity.is_finite()
            or not Decimal(0) <= self.executed_quantity <= self.quantity
            or self.opened_at.tzinfo is None
        ):
            raise EvidenceError("INVALID_CANONICAL_OPEN_ORDER")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class AccountOpenOrdersEvidence(EvidenceRecord):
    venue: VenueId
    credential_reference_identity: ContentIdentity
    capability_identity: ContentIdentity
    orders: tuple[CanonicalOpenOrder, ...]
    scope: str
    complete: bool
    observed_at: datetime
    source_identity: ContentIdentity
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        ids = tuple(order.exchange_order_id for order in self.orders)
        if (
            self.venue is not VenueId.KRAKEN
            or type(self.credential_reference_identity) is not ContentIdentity
            or type(self.capability_identity) is not ContentIdentity
            or ids != tuple(sorted(ids))
            or len(set(ids)) != len(ids)
            or self.scope != "ACCOUNT_WIDE"
            or self.complete is not True
            or self.observed_at.tzinfo is None
            or type(self.source_identity) is not ContentIdentity
        ):
            raise EvidenceError("INVALID_ACCOUNT_OPEN_ORDERS_EVIDENCE")
        EvidenceRecord.__post_init__(self)


class PrivateReadOnlyExchangePort(Protocol):
    """Observation-only port. There is deliberately no order or funding method."""

    venue: VenueId

    def credential_capability(
        self, reference: PrivateCredentialReference, observed_at: datetime
    ) -> PrivateCredentialCapabilityEvidence: ...

    def balances(
        self,
        reference: PrivateCredentialReference,
        capability: PrivateCredentialCapabilityEvidence,
        observed_at: datetime,
    ) -> AccountBalanceEvidence: ...

    def open_orders(
        self,
        reference: PrivateCredentialReference,
        capability: PrivateCredentialCapabilityEvidence,
        observed_at: datetime,
    ) -> AccountOpenOrdersEvidence: ...
