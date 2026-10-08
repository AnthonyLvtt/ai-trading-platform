"""The qualification runner is exercised only with an in-memory transport."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atp.exchange.kraken.exposure_transport import (
    KrakenExposureHTTPObservation,
    KrakenExposureTransportError,
)
from atp.exchange.kraken.private import parse_api_key_info
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.exchange.read_only import encoded
from atp.kraken_private_qualification.exposure_connectivity import (
    qualify_exposure_connectivity,
)
from atp.kraken_private_qualification.exposure_connectivity_model import (
    ExposureConnectivityReason,
    ExposureConnectivityStatus,
)
from atp.kraken_private_qualification.exposure_observation_model import OfflineExposureRoute
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity
from tests.unit.test_kraken_private import fixture
from tests.unit.test_oms_offline_evidence_sources import (
    balance_ex_payload,
    trade_volume_payload,
)

AT = datetime(2026, 10, 7, 12, tzinfo=UTC)
SHA = "a" * 40


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


class Transport:
    def __init__(self, *, fail_at: int = 0, wrong_identity: bool = False) -> None:
        self.calls = 0
        self.fail_at = fail_at
        self.wrong_identity = wrong_identity
        self.payloads = (balance_ex_payload(), trade_volume_payload())

    def post(self, request, credential, nonce_provider):
        assert nonce_provider.reference_identity == credential.reference.content_identity
        self.calls += 1
        if self.calls == self.fail_at:
            raise KrakenExposureTransportError("SIMULATED", network_call_performed=True)
        moment = AT + timedelta(seconds=self.calls)
        return KrakenExposureHTTPObservation(
            request.route,
            ContentIdentity.from_text("foreign")
            if self.wrong_identity
            else request.content_identity,
            self.payloads[self.calls - 1],
            moment,
            moment,
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
        "atp.kraken_private_qualification.exposure_connectivity.inspect_source",
        lambda root: source,
    )
    return source


def run(loader: Loader, transport: Transport, *, capability=None, account=None):
    credential = EphemeralKrakenCredential(
        bytearray(b"synthetic-public-key"), bytearray(b"c2VjcmV0")
    )
    default_capability = parse_api_key_info(fixture("api-key-info.json"), credential.reference, AT)
    credential.close()
    times = iter((AT, AT + timedelta(seconds=3)))
    return qualify_exposure_connectivity(
        expected_source_sha=SHA,
        source_root=Path.cwd(),
        credential_loader=loader,
        capability=default_capability if capability is None else capability,
        account_identity=ContentIdentity.from_text("account") if account is None else account,
        transport=transport,
        clock=lambda: next(times),
    )


def test_two_reads_are_source_bound_counted_and_sanitized(clean_main) -> None:
    loader, transport = Loader(), Transport()
    transport.payloads[1]["result"]["metadata"] = {"key": "SYNTHETIC-SECRET"}
    result = run(loader, transport)
    assert result.status is ExposureConnectivityStatus.PASSED
    assert result.source_identity == clean_main.content_identity
    assert result.private_network_calls == transport.calls == 2
    assert result.completed_routes == (
        OfflineExposureRoute.BALANCE_EX,
        OfflineExposureRoute.TRADE_VOLUME,
    )
    assert result.real_economic_calls == 0
    assert result.runtime_pass_qualified is False
    assert result.live == "LIVE_FORBIDDEN"
    assert result.proofs[0].account_identity == result.proofs[1].account_identity
    assert result.proofs[0].capability_identity == result.capability_identity
    assert "SYNTHETIC-SECRET" not in json.dumps(encoded(result))
    assert loader.last is not None
    with pytest.raises(ValueError, match="KRAKEN_CREDENTIAL_UNAVAILABLE"):
        loader.last.api_key_bytes()


def test_source_or_binding_failure_blocks_before_transport(clean_main, monkeypatch) -> None:
    loader, transport = Loader(), Transport()
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_connectivity.inspect_source",
        lambda root: replace(clean_main, source_commit_sha="b" * 40),
    )
    result = run(loader, transport)
    assert result.reason_code is ExposureConnectivityReason.SOURCE_INVALID
    assert loader.calls == transport.calls == result.private_network_calls == 0
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_connectivity.inspect_source",
        lambda root: clean_main,
    )
    bad = run(loader, transport, account="foreign")
    assert bad.reason_code is ExposureConnectivityReason.BINDING_INVALID
    assert transport.calls == 0


def test_failure_after_one_call_stops_and_counts_attempt(clean_main) -> None:
    loader, transport = Loader(), Transport(fail_at=2)
    result = run(loader, transport)
    assert result.status is ExposureConnectivityStatus.FAILED
    assert result.reason_code is ExposureConnectivityReason.NETWORK_FAILURE
    assert result.private_network_calls == transport.calls == 2
    assert result.completed_routes == (OfflineExposureRoute.BALANCE_EX,)
    assert len(result.proofs) == 1
    assert result.source_identity is None


def test_wrong_response_binding_and_malformed_evidence_fail_closed(clean_main) -> None:
    wrong = run(Loader(), Transport(wrong_identity=True))
    assert wrong.reason_code is ExposureConnectivityReason.RESPONSE_INVALID
    assert wrong.private_network_calls == 1
    transport = Transport()
    transport.payloads[0]["result"].pop("ZEUR")
    invalid = run(Loader(), transport)
    assert invalid.reason_code is ExposureConnectivityReason.BALANCE_EUR_MISSING
    assert invalid.private_network_calls == 1


def test_stale_capability_blocks_before_credential_loading(clean_main) -> None:
    credential = EphemeralKrakenCredential(
        bytearray(b"synthetic-public-key"), bytearray(b"c2VjcmV0")
    )
    capability = parse_api_key_info(
        fixture("api-key-info.json"), credential.reference, AT - timedelta(seconds=31)
    )
    credential.close()
    loader, transport = Loader(), Transport()
    result = run(loader, transport, capability=capability)
    assert result.reason_code is ExposureConnectivityReason.BINDING_INVALID
    assert loader.calls == transport.calls == 0


def test_api_rejection_is_sanitized_and_stops_after_first_call(clean_main) -> None:
    transport = Transport()
    transport.payloads = ({"error": ["EAPI:Invalid key"], "result": {}}, transport.payloads[1])
    result = run(Loader(), transport)
    assert result.reason_code is ExposureConnectivityReason.API_REJECTED
    assert result.private_network_calls == 1
    assert result.proofs == ()
    assert "EAPI:Invalid key" not in json.dumps(encoded(result))


def test_changed_source_after_observation_invalidates_result(clean_main, monkeypatch) -> None:
    values = iter((clean_main, replace(clean_main, source_commit_sha="b" * 40)))
    monkeypatch.setattr(
        "atp.kraken_private_qualification.exposure_connectivity.inspect_source",
        lambda root: next(values),
    )
    result = run(Loader(), Transport())
    assert result.reason_code is ExposureConnectivityReason.SOURCE_INVALID
    assert result.private_network_calls == 2
    assert result.source_identity is None
