from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from atp.oms.exposure_qualification import (
    BOUNDED_FEE_SOURCE,
    EXPOSURE_EVIDENCE_QUALIFICATION_V1,
    EXPOSURE_FRESHNESS_POLICY_V1,
    EXPOSURE_SOURCE_REGISTRY,
    SPENDABLE_EUR_SOURCE,
    ExposureQualificationError,
    ExposureSourceKind,
)


def test_source_registry_qualifies_existing_reads_but_keeps_missing_sources_closed() -> None:
    by_kind = {source.kind: source for source in EXPOSURE_SOURCE_REGISTRY}
    assert by_kind[ExposureSourceKind.ACCOUNT_BALANCE].runtime_authorized is True
    assert by_kind[ExposureSourceKind.ACCOUNT_OPEN_ORDERS].runtime_authorized is True
    assert by_kind[ExposureSourceKind.VALUATION_PRICE].runtime_authorized is True
    assert SPENDABLE_EUR_SOURCE.route == "/0/private/BalanceEx"
    assert SPENDABLE_EUR_SOURCE.runtime_authorized is False
    assert SPENDABLE_EUR_SOURCE.observation_qualified is False
    assert BOUNDED_FEE_SOURCE.route is None
    assert BOUNDED_FEE_SOURCE.runtime_authorized is False
    assert BOUNDED_FEE_SOURCE.observation_qualified is False


def test_freshness_policy_is_exact_and_fail_closed() -> None:
    policy = EXPOSURE_FRESHNESS_POLICY_V1
    assert policy.maximum_age_for(ExposureSourceKind.SPENDABLE_EUR) == Decimal("30")
    assert policy.maximum_age_for(ExposureSourceKind.BOUNDED_FEE) == Decimal("86400")
    assert policy.maximum_age_for(ExposureSourceKind.ACCOUNT_BALANCE) == Decimal("30")
    assert policy.maximum_age_for(ExposureSourceKind.ACCOUNT_OPEN_ORDERS) == Decimal("30")
    assert policy.maximum_age_for(ExposureSourceKind.VALUATION_PRICE) == Decimal("10")
    assert policy.maximum_bundle_skew_seconds == Decimal("30")
    with pytest.raises(ExposureQualificationError, match="EXPOSURE_SOURCE_KIND_INVALID"):
        policy.maximum_age_for("SPENDABLE_EUR")  # type: ignore[arg-type]
    with pytest.raises(ExposureQualificationError, match="EXPOSURE_FRESHNESS_POLICY_INVALID"):
        replace(policy, spendable_eur_max_age_seconds=Decimal("0"))


def test_runtime_pass_remains_unqualified_until_both_blocking_sources_are_qualified() -> None:
    result = EXPOSURE_EVIDENCE_QUALIFICATION_V1
    assert result.offline_contracts_qualified is True
    assert result.runtime_pass_qualified is False
    assert result.blocking_source_kinds == (
        ExposureSourceKind.SPENDABLE_EUR,
        ExposureSourceKind.BOUNDED_FEE,
    )
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"


def test_source_contract_cannot_claim_runtime_authority_without_observation_qualification() -> None:
    with pytest.raises(ExposureQualificationError, match="EXPOSURE_SOURCE_CONTRACT_INVALID"):
        replace(SPENDABLE_EUR_SOURCE, runtime_authorized=True)
