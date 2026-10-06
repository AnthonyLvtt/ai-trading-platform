"""Synthetic qualification only: no credential loader or network transport."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atp.exchange.kraken.private import (
    KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST,
    KrakenPrivateError,
    sign_private_read_request,
)
from atp.exchange.kraken.private_transport import KrakenPrivateTransportError, _route
from atp.exchange.read_only import EvidenceError, encoded
from atp.kraken_private_qualification.exposure_observation import (
    exact_offline_exposure_requests,
    qualify_offline_exposure_observations,
)
from atp.kraken_private_qualification.exposure_observation_model import (
    OfflineExposureObservation,
    OfflineExposureReason,
    OfflineExposureRoute,
    OfflineExposureStatus,
    OfflineExposureValueKind,
)
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity
from tests.unit.test_kraken_private import fixture, reference
from tests.unit.test_oms_offline_evidence_sources import (
    balance_ex_payload,
    trade_volume_payload,
)

AT = datetime(2026, 10, 7, 12, tzinfo=UTC)
BASE = "70c9eea36168c034d652c432abe895bc554febbe"


def inputs():
    from atp.exchange.kraken.private import parse_api_key_info

    ref = reference()
    capability = parse_api_key_info(fixture("api-key-info.json"), ref, AT)
    account = ContentIdentity.from_text("synthetic-default-wallet")
    requests = exact_offline_exposure_requests(ref, capability, account)
    observations = (
        OfflineExposureObservation(
            OfflineExposureRoute.BALANCE_EX,
            requests[0].content_identity,
            balance_ex_payload(),
            AT,
        ),
        OfflineExposureObservation(
            OfflineExposureRoute.TRADE_VOLUME,
            requests[1].content_identity,
            trade_volume_payload(),
            AT + timedelta(seconds=1),
        ),
    )
    return ref, capability, account, requests, observations


@pytest.fixture(autouse=True)
def clean_main_source(monkeypatch: pytest.MonkeyPatch):
    source = replace(inspect_source(Path.cwd()), clean=True, source_branch="main")
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_observation.inspect_source",
        lambda root: source,
    )
    return source


def qualify(*, observations=None, account=None):
    ref, capability, default_account, _, default_observations = inputs()
    return qualify_offline_exposure_observations(
        expected_source_sha=BASE,
        source_root=Path.cwd(),
        reference=ref,
        capability=capability,
        account_identity=default_account if account is None else account,
        observations=default_observations if observations is None else observations,
    )


def test_exact_requests_cannot_reach_existing_transport() -> None:
    _, _, _, requests, _ = inputs()
    assert requests[0].parameters == ()
    assert requests[1].parameters == (("pair", "XXBTZEUR"),)
    assert requests[0].scope == "DEFAULT_WALLET"
    assert requests[1].scope == "ACCOUNT_PAIR_BTC_EUR"
    assert not {route.value for route in OfflineExposureRoute} & KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
    for request in requests:
        with pytest.raises(KrakenPrivateTransportError, match="KRAKEN_PRIVATE_ROUTE_FORBIDDEN"):
            _route(request)  # type: ignore[arg-type]
        with pytest.raises(KrakenPrivateError, match="KRAKEN_PRIVATE_ROUTE_FORBIDDEN"):
            sign_private_read_request(request.route, 1, "c2VjcmV0")  # type: ignore[arg-type]
    with pytest.raises(EvidenceError, match="OFFLINE_EXPOSURE_REQUEST_INVALID"):
        replace(requests[1], parameters=(("pair", "XXBTZUSD"),))
    with pytest.raises(EvidenceError, match="OFFLINE_EXPOSURE_REQUEST_INVALID"):
        replace(requests[1], scope="DEFAULT_WALLET")


def test_offline_pass_binds_source_account_capability_and_sanitizes(clean_main_source) -> None:
    _, _, _, _, observations = inputs()
    fee_payload = trade_volume_payload()
    fee_payload["result"]["metadata"] = {"marker": "SYNTHETIC-SECRET"}
    result = qualify(observations=(observations[0], replace(observations[1], payload=fee_payload)))
    assert result.status is OfflineExposureStatus.PASSED
    assert result.source_identity == clean_main_source.content_identity
    assert result.source_commit_sha == BASE
    assert result.offline_observations == 2
    assert result.private_network_calls == result.real_economic_calls == 0
    assert result.observation_qualified is result.runtime_pass_qualified is False
    assert [proof.value_kind for proof in result.proofs] == [
        OfflineExposureValueKind.SPENDABLE_EUR,
        OfflineExposureValueKind.TAKER_MAX_FEE_PERCENT,
    ]
    assert all(proof.account_identity == result.account_identity for proof in result.proofs)
    assert all(proof.capability_identity == result.capability_identity for proof in result.proofs)
    assert all(
        proof.credential_reference_identity == result.credential_reference_identity
        for proof in result.proofs
    )
    text = json.dumps(encoded(result), sort_keys=True)
    assert "1000.00" not in text
    assert "tiervolume" not in text
    assert "SYNTHETIC-SECRET" not in text


def test_wrong_request_or_order_fails_without_network_call() -> None:
    _, _, _, _, observations = inputs()
    wrong = replace(observations[0], request_identity=ContentIdentity.from_text("foreign"))
    result = qualify(observations=(wrong, observations[1]))
    assert result.status is OfflineExposureStatus.FAILED
    assert result.reason_code is OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID
    assert result.private_network_calls == 0
    reversed_result = qualify(observations=tuple(reversed(observations)))
    assert reversed_result.reason_code is OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID


def test_missing_eur_or_fee_pair_fails_closed() -> None:
    _, _, _, _, observations = inputs()
    balance = balance_ex_payload()
    del balance["result"]["ZEUR"]
    broken_balance = replace(observations[0], payload=balance)
    assert qualify(observations=(broken_balance, observations[1])).reason_code is (
        OfflineExposureReason.OBSERVATION_PAYLOAD_INVALID
    )
    fees = trade_volume_payload()
    fees["result"]["fees"] = {}
    broken_fee = replace(observations[1], payload=fees)
    assert qualify(observations=(observations[0], broken_fee)).reason_code is (
        OfflineExposureReason.OBSERVATION_PAYLOAD_INVALID
    )


def test_stale_or_extra_observation_fails() -> None:
    _, _, _, _, observations = inputs()
    before_capability = replace(observations[0], observed_at=AT - timedelta(seconds=1))
    assert qualify(observations=(before_capability, observations[1])).reason_code is (
        OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID
    )
    late = replace(observations[1], observed_at=AT + timedelta(seconds=31))
    assert qualify(observations=(observations[0], late)).reason_code is (
        OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID
    )
    assert qualify(observations=(*observations, observations[1])).reason_code is (
        OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID
    )


def test_changed_or_wrong_source_fails(monkeypatch: pytest.MonkeyPatch, clean_main_source) -> None:
    values = iter((clean_main_source, replace(clean_main_source, source_commit_sha="b" * 40)))
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_observation.inspect_source",
        lambda root: next(values),
    )
    result = qualify()
    assert result.status is OfflineExposureStatus.FAILED
    assert result.reason_code is OfflineExposureReason.SOURCE_INVALID
    assert result.source_identity is None


def test_account_binding_mismatch_fails() -> None:
    foreign_account = ContentIdentity.from_text("foreign-account")
    result = qualify(account=foreign_account)
    assert result.reason_code is OfflineExposureReason.OBSERVATION_SEQUENCE_INVALID
    assert result.private_network_calls == 0


def test_credential_capability_binding_mismatch_fails() -> None:
    ref, capability, account, _, observations = inputs()
    foreign = replace(ref, reference_id="b" * 32)
    result = qualify_offline_exposure_observations(
        expected_source_sha=BASE,
        source_root=Path.cwd(),
        reference=foreign,
        capability=capability,
        account_identity=account,
        observations=observations,
    )
    assert result.status is OfflineExposureStatus.FAILED
    assert result.reason_code is OfflineExposureReason.BINDING_INVALID
    assert result.private_network_calls == 0
