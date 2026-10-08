"""All operator-gate tests use synthetic credentials and in-memory transports."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atp.exchange.kraken.exposure_transport import KrakenExposureHTTPObservation
from atp.exchange.kraken.private import KrakenPrivateReadRoute
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.exchange.kraken.private_transport import KrakenPrivateHTTPObservation
from atp.exchange.read_only import EvidenceError, encoded
from atp.kraken_private_qualification.exposure_gate import (
    account_identity_from_iiban,
    qualify_exposure_operator_gate,
)
from atp.kraken_private_qualification.exposure_gate_model import (
    ExposureGateReason,
    ExposureGateStatus,
)
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity
from tests.unit.test_kraken_private import fixture
from tests.unit.test_oms_offline_evidence_sources import (
    balance_ex_payload,
    trade_volume_payload,
)

AT = datetime(2026, 10, 7, 12, tzinfo=UTC)
SHA = "a" * 40
IIBAN = "TEST-IIBAN-NEVER-PERSISTED"


def test_iiban_fingerprint_is_canonical_and_rejects_missing_value() -> None:
    assert account_identity_from_iiban(IIBAN) == account_identity_from_iiban(
        "test iiban never persisted"
    )
    with pytest.raises(EvidenceError):
        account_identity_from_iiban("")


class Loader:
    def __init__(self) -> None:
        self.calls = 0
        self.last: EphemeralKrakenCredential | None = None

    def __call__(self) -> EphemeralKrakenCredential:
        self.calls += 1
        self.last = EphemeralKrakenCredential(
            bytearray(b"synthetic-public-key"), bytearray(b"c2VjcmV0")
        )
        return self.last


class KeyInfoTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.payload = fixture("api-key-info.json")
        self.payload["result"]["apiKey"] = "synthetic-public-key"
        self.payload["result"]["iban"] = IIBAN

    def post(self, request, credential, nonce_provider):
        assert request.route is KrakenPrivateReadRoute.API_KEY_INFO
        assert request.credential_reference_identity == credential.reference.content_identity
        assert nonce_provider.reference_identity == credential.reference.content_identity
        self.calls += 1
        return KrakenPrivateHTTPObservation(
            request.route, self.payload, AT, AT + timedelta(seconds=1)
        )


class ExposureTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.payloads = (balance_ex_payload(), trade_volume_payload())

    def post(self, request, credential, nonce_provider):
        assert request.credential_reference_identity == credential.reference.content_identity
        assert nonce_provider.reference_identity == credential.reference.content_identity
        self.calls += 1
        moment = AT + timedelta(seconds=self.calls + 1)
        return KrakenExposureHTTPObservation(
            request.route, request.content_identity, self.payloads[self.calls - 1], moment, moment
        )


@pytest.fixture
def clean_main(monkeypatch):
    source = replace(
        inspect_source(Path.cwd()),
        clean=True,
        source_branch="main",
        source_commit_sha=SHA,
    )
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_gate.inspect_source", lambda root: source
    )
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_connectivity.inspect_source",
        lambda root: source,
    )
    return source


def run(loader, key_info, exposure, *, account=None):
    times = iter(
        (
            AT,
            AT + timedelta(seconds=1),
            AT + timedelta(seconds=4),
            AT + timedelta(seconds=5),
        )
    )
    return qualify_exposure_operator_gate(
        expected_source_sha=SHA,
        source_root=Path.cwd(),
        expected_account_identity=(
            account_identity_from_iiban(IIBAN) if account is None else account
        ),
        credential_loader=loader,
        key_info_transport=key_info,
        exposure_transport=exposure,
        clock=lambda: next(times),
    )


def test_exact_three_call_sequence_is_bound_and_sanitized(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    result = run(loader, key_info, exposure)
    assert result.status is ExposureGateStatus.PASSED
    assert result.source_identity == clean_main.content_identity
    assert result.completed_routes == (
        "/0/private/GetApiKeyInfo",
        "/0/private/BalanceEx",
        "/0/private/TradeVolume",
    )
    assert result.total_private_network_calls == key_info.calls + exposure.calls == 3
    assert result.account_identity == account_identity_from_iiban(IIBAN)
    assert result.real_economic_calls == 0
    assert result.runtime_pass_qualified is False
    assert result.live == "LIVE_FORBIDDEN"
    serialized = json.dumps(encoded(result), sort_keys=True)
    assert IIBAN not in serialized
    assert "synthetic-public-key" not in serialized
    assert "1000.00" not in serialized
    assert loader.last is not None
    with pytest.raises(ValueError, match="KRAKEN_CREDENTIAL_UNAVAILABLE"):
        loader.last.api_key_bytes()


def test_source_must_match_before_credential_or_network(clean_main, monkeypatch) -> None:
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_gate._source",
        lambda root, sha: (_ for _ in ()).throw(ValueError("SOURCE_INVALID")),
    )
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.SOURCE_INVALID
    assert result.total_private_network_calls == 0
    assert loader.calls == key_info.calls == exposure.calls == 0


def test_missing_api_key_echo_is_accepted(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    del key_info.payload["result"]["apiKey"]
    result = run(loader, key_info, exposure)
    assert result.status is ExposureGateStatus.PASSED
    assert result.total_private_network_calls == 3
    assert exposure.calls == 2


def test_missing_iiban_fails_before_exposure(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    del key_info.payload["result"]["iban"]
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.IIBAN_FIELD_INVALID
    assert result.total_private_network_calls == 1
    assert exposure.calls == 0


def test_non_string_api_key_field_is_sanitized(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    key_info.payload["result"]["apiKey"] = None
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.API_KEY_FIELD_TYPE_INVALID
    assert result.total_private_network_calls == 1
    assert exposure.calls == 0


def test_invalid_iiban_format_is_sanitized(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    key_info.payload["result"]["iban"] = "invalid!"
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.IIBAN_FORMAT_INVALID
    assert result.total_private_network_calls == 1
    assert exposure.calls == 0
    serialized = json.dumps(encoded(result), sort_keys=True)
    assert "invalid!" not in serialized


def test_foreign_account_or_key_reports_sanitized_mismatch(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    wrong = run(loader, key_info, exposure, account=ContentIdentity.from_text("foreign"))
    assert wrong.reason_code is ExposureGateReason.IIBAN_MISMATCH
    assert wrong.total_private_network_calls == 1
    assert exposure.calls == 0

    key_info.payload["result"]["apiKey"] = "foreign-key"
    wrong_key = run(Loader(), key_info, exposure)
    assert wrong_key.reason_code is ExposureGateReason.API_KEY_MISMATCH
    assert wrong_key.total_private_network_calls == 1
    assert exposure.calls == 0

    serialized = json.dumps(encoded(wrong_key), sort_keys=True)
    assert "synthetic-public-key" not in serialized
    assert "foreign-key" not in serialized
    assert IIBAN not in serialized


def test_extra_permission_blocks_before_exposure(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    key_info.payload["result"]["permissions"].append("modify-trades")
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.LEAST_PRIVILEGE_REQUIRED
    assert result.total_private_network_calls == 1
    assert exposure.calls == 0


def test_missing_credit_field_fails_closed_after_balance_ex(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    del exposure.payloads[0]["result"]["ZEUR"]["credit_used"]
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.EXPOSURE_EVIDENCE_INVALID
    assert result.total_private_network_calls == 2
    assert exposure.calls == 1
    assert result.source_identity is None


def test_source_change_after_key_info_stops_exposure(clean_main, monkeypatch) -> None:
    values = iter((clean_main, replace(clean_main, source_commit_sha="b" * 40)))
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_gate._source",
        lambda root, sha: next(values),
    )
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_connectivity.inspect_source",
        lambda root: next(values),
    )
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.EXPOSURE_INVALID
    assert result.total_private_network_calls == 1
    assert exposure.calls == 0


def test_source_change_after_all_reads_invalidates_gate(clean_main, monkeypatch) -> None:
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_gate.inspect_source",
        lambda root: replace(clean_main, source_commit_sha="b" * 40),
    )
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.SOURCE_INVALID
    assert result.total_private_network_calls == 3
    assert result.source_identity is None


def test_stale_key_info_response_blocks_exposure(clean_main) -> None:
    class StaleKeyInfoTransport(KeyInfoTransport):
        def post(self, request, credential, nonce_provider):
            observation = super().post(request, credential, nonce_provider)
            return replace(observation, observed_at=AT + timedelta(seconds=31))

    loader, key_info, exposure = Loader(), StaleKeyInfoTransport(), ExposureTransport()
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.KEY_INFO_INVALID
    assert result.total_private_network_calls == 1
    assert exposure.calls == 0


def test_wrong_key_info_route_is_not_reported_completed(clean_main) -> None:
    class WrongRouteTransport(KeyInfoTransport):
        def post(self, request, credential, nonce_provider):
            observation = super().post(request, credential, nonce_provider)
            return replace(observation, route=KrakenPrivateReadRoute.BALANCE)

    loader, key_info, exposure = Loader(), WrongRouteTransport(), ExposureTransport()
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.KEY_INFO_INVALID
    assert result.total_private_network_calls == 1
    assert result.completed_routes == ()
    assert exposure.calls == 0


def test_kraken_api_rejection_reports_sanitized_reason(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    exposure.payloads = (
        {"error": ["EGeneral:Permission denied"], "result": {}},
        exposure.payloads[1],
    )
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.EXPOSURE_API_REJECTED
    assert result.total_private_network_calls == 2
    assert exposure.calls == 1
    assert "Permission denied" not in json.dumps(encoded(result))

def test_balanceex_unsupported_asset_reports_sanitized_reason(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    exposure.payloads[0]["result"]["SECRET_ASSET"] = {
        "balance": "123456.78",
        "hold_trade": "0",
        "credit": "0",
        "credit_used": "0",
    }
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.BALANCE_ASSET_UNSUPPORTED
    assert result.total_private_network_calls == 2
    assert exposure.calls == 1
    serialized = json.dumps(encoded(result))
    assert "SECRET_ASSET" not in serialized
    assert "123456.78" not in serialized


def test_balanceex_missing_eur_reports_sanitized_reason(clean_main) -> None:
    loader, key_info, exposure = Loader(), KeyInfoTransport(), ExposureTransport()
    del exposure.payloads[0]["result"]["ZEUR"]
    result = run(loader, key_info, exposure)
    assert result.reason_code is ExposureGateReason.BALANCE_EUR_MISSING
    assert result.total_private_network_calls == 2
    assert exposure.calls == 1

