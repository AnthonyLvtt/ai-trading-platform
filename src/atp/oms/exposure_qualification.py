"""Offline qualification policy for OMS exposure evidence sources.

This module records which evidence sources are accepted in principle and their
maximum ages. It deliberately does not create spendable-funds or fee evidence,
does not open new Kraken routes, and cannot authorize an exposure PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from atp.exchange.contracts import VenueId
from atp.exchange.read_only import EvidenceRecord
from atp.shared.identity import ContentIdentity


class ExposureQualificationError(ValueError):
    pass


class ExposureSourceKind(StrEnum):
    ACCOUNT_BALANCE = "ACCOUNT_BALANCE"
    ACCOUNT_OPEN_ORDERS = "ACCOUNT_OPEN_ORDERS"
    SPENDABLE_EUR = "SPENDABLE_EUR"
    BOUNDED_FEE = "BOUNDED_FEE"
    VALUATION_PRICE = "VALUATION_PRICE"


@dataclass(frozen=True, slots=True)
class ExposureSourceContract(EvidenceRecord):
    kind: ExposureSourceKind
    venue: VenueId
    mechanism: str
    route: str | None
    observation_qualified: bool
    runtime_authorized: bool
    real_economic_calls: int = 0
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ExposureSourceKind
            or self.venue is not VenueId.KRAKEN
            or type(self.mechanism) is not str
            or not self.mechanism
            or (self.route is not None and not self.route.startswith("/0/"))
            or type(self.observation_qualified) is not bool
            or type(self.runtime_authorized) is not bool
            or self.runtime_authorized and not self.observation_qualified
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
        ):
            raise ExposureQualificationError("EXPOSURE_SOURCE_CONTRACT_INVALID")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class ExposureFreshnessPolicy(EvidenceRecord):
    spendable_eur_max_age_seconds: Decimal
    bounded_fee_max_age_seconds: Decimal
    account_balance_max_age_seconds: Decimal
    open_orders_max_age_seconds: Decimal
    valuation_price_max_age_seconds: Decimal
    maximum_bundle_skew_seconds: Decimal
    policy_version: str = "1.0.0"
    live: str = "LIVE_FORBIDDEN"

    def __post_init__(self) -> None:
        values = (
            self.spendable_eur_max_age_seconds,
            self.bounded_fee_max_age_seconds,
            self.account_balance_max_age_seconds,
            self.open_orders_max_age_seconds,
            self.valuation_price_max_age_seconds,
            self.maximum_bundle_skew_seconds,
        )
        if (
            any(type(value) is not Decimal or not value.is_finite() or value <= 0 for value in values)
            or self.policy_version != "1.0.0"
            or self.live != "LIVE_FORBIDDEN"
        ):
            raise ExposureQualificationError("EXPOSURE_FRESHNESS_POLICY_INVALID")
        EvidenceRecord.__post_init__(self)

    def maximum_age_for(self, kind: ExposureSourceKind) -> Decimal:
        mapping = {
            ExposureSourceKind.SPENDABLE_EUR: self.spendable_eur_max_age_seconds,
            ExposureSourceKind.BOUNDED_FEE: self.bounded_fee_max_age_seconds,
            ExposureSourceKind.ACCOUNT_BALANCE: self.account_balance_max_age_seconds,
            ExposureSourceKind.ACCOUNT_OPEN_ORDERS: self.open_orders_max_age_seconds,
            ExposureSourceKind.VALUATION_PRICE: self.valuation_price_max_age_seconds,
        }
        if type(kind) is not ExposureSourceKind:
            raise ExposureQualificationError("EXPOSURE_SOURCE_KIND_INVALID")
        return mapping[kind]


EXPOSURE_FRESHNESS_POLICY_V1 = ExposureFreshnessPolicy(
    spendable_eur_max_age_seconds=Decimal("30"),
    bounded_fee_max_age_seconds=Decimal("86400"),
    account_balance_max_age_seconds=Decimal("30"),
    open_orders_max_age_seconds=Decimal("30"),
    valuation_price_max_age_seconds=Decimal("10"),
    maximum_bundle_skew_seconds=Decimal("30"),
)

ACCOUNT_BALANCE_SOURCE = ExposureSourceContract(
    kind=ExposureSourceKind.ACCOUNT_BALANCE,
    venue=VenueId.KRAKEN,
    mechanism="KRAKEN_PRIVATE_BALANCE",
    route="/0/private/Balance",
    observation_qualified=True,
    runtime_authorized=True,
)

ACCOUNT_OPEN_ORDERS_SOURCE = ExposureSourceContract(
    kind=ExposureSourceKind.ACCOUNT_OPEN_ORDERS,
    venue=VenueId.KRAKEN,
    mechanism="KRAKEN_PRIVATE_OPEN_ORDERS",
    route="/0/private/OpenOrders",
    observation_qualified=True,
    runtime_authorized=True,
)

SPENDABLE_EUR_SOURCE = ExposureSourceContract(
    kind=ExposureSourceKind.SPENDABLE_EUR,
    venue=VenueId.KRAKEN,
    mechanism="KRAKEN_BALANCE_EX",
    route="/0/private/BalanceEx",
    observation_qualified=False,
    runtime_authorized=False,
)

BOUNDED_FEE_SOURCE = ExposureSourceContract(
    kind=ExposureSourceKind.BOUNDED_FEE,
    venue=VenueId.KRAKEN,
    mechanism="KRAKEN_SPOT_FEE_SCHEDULE",
    route=None,
    observation_qualified=False,
    runtime_authorized=False,
)

VALUATION_PRICE_SOURCE = ExposureSourceContract(
    kind=ExposureSourceKind.VALUATION_PRICE,
    venue=VenueId.KRAKEN,
    mechanism="KRAKEN_PUBLIC_TICKER",
    route="/0/public/Ticker",
    observation_qualified=True,
    runtime_authorized=True,
)

EXPOSURE_SOURCE_REGISTRY = (
    ACCOUNT_BALANCE_SOURCE,
    ACCOUNT_OPEN_ORDERS_SOURCE,
    SPENDABLE_EUR_SOURCE,
    BOUNDED_FEE_SOURCE,
    VALUATION_PRICE_SOURCE,
)


@dataclass(frozen=True, slots=True)
class ExposureEvidenceQualification(EvidenceRecord):
    freshness_policy_identity: ContentIdentity
    source_contract_identities: tuple[ContentIdentity, ...]
    offline_contracts_qualified: bool
    runtime_pass_qualified: bool
    blocking_source_kinds: tuple[ExposureSourceKind, ...]
    real_economic_calls: int = 0
    live: str = "LIVE_FORBIDDEN"
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.freshness_policy_identity) is not ContentIdentity
            or self.source_contract_identities
            != tuple(source.content_identity for source in EXPOSURE_SOURCE_REGISTRY)
            or self.offline_contracts_qualified is not True
            or type(self.runtime_pass_qualified) is not bool
            or self.blocking_source_kinds
            != tuple(
                source.kind
                for source in EXPOSURE_SOURCE_REGISTRY
                if not source.runtime_authorized
            )
            or self.runtime_pass_qualified != (len(self.blocking_source_kinds) == 0)
            or self.real_economic_calls != 0
            or self.live != "LIVE_FORBIDDEN"
            or self.side_effect_performed is not False
        ):
            raise ExposureQualificationError("EXPOSURE_EVIDENCE_QUALIFICATION_INVALID")
        EvidenceRecord.__post_init__(self)


EXPOSURE_EVIDENCE_QUALIFICATION_V1 = ExposureEvidenceQualification(
    freshness_policy_identity=EXPOSURE_FRESHNESS_POLICY_V1.content_identity,
    source_contract_identities=tuple(
        source.content_identity for source in EXPOSURE_SOURCE_REGISTRY
    ),
    offline_contracts_qualified=True,
    runtime_pass_qualified=False,
    blocking_source_kinds=(
        ExposureSourceKind.SPENDABLE_EUR,
        ExposureSourceKind.BOUNDED_FEE,
    ),
)
