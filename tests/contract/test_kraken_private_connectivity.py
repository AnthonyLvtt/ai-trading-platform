from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.private import (
    KrakenPrivateReadRoute,
    account_wide_open_orders_request,
    parse_api_key_info,
    parse_balances,
    parse_open_orders,
)
from atp.exchange.kraken.private_credentials import EphemeralKrakenCredential
from atp.exchange.kraken.private_transport import (
    KrakenPrivateHTTPObservation,
    KrakenPrivateTransportError,
)
from atp.exchange.private_contracts import PrivateCredentialReference
from atp.exchange.read_only import EvidenceError, encoded
from atp.kraken_private_qualification import (
    KrakenPrivateConnectivityReason,
    KrakenPrivateConnectivityStatus,
    qualify_private_connectivity,
)
from atp.release_deployment.source import inspect_source
from atp.shared.environment import Environment

AT = datetime(2026, 9, 29, 12, tzinfo=UTC)
SHA = "a" * 40
FIXTURES = Path(__file__).parents[1] / "fixtures" / "kraken" / "private"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


class Loader:
    def __init__(self) -> None:
        self.calls = 0
        self.credential: EphemeralKrakenCredential | None = None

    def __call__(self) -> EphemeralKrakenCredential:
        self.calls += 1
        self.credential = EphemeralKrakenCredential(
            bytearray(b"fixture-public-key-never-persisted"),
            bytearray(b"c2VjcmV0"),
        )
        return self.credential


class FixtureTransport:
    def __init__(self, *, failure: str | None = None) -> None:
        self.calls = []
        self.failure = failure

    def post(self, request, credential, nonce_provider):
        del credential, nonce_provider
        self.calls.append(request.route)
        if self.failure is not None:
            raise KrakenPrivateTransportError(self.failure, network_call_performed=True)
        names = {
            KrakenPrivateReadRoute.API_KEY_INFO: "api-key-info.json",
            KrakenPrivateReadRoute.BALANCE: "balance.json",
            KrakenPrivateReadRoute.OPEN_ORDERS: "open-orders.json",
        }
        observed = AT + timedelta(seconds=len(self.calls))
        return KrakenPrivateHTTPObservation(
            request.route, fixture(names[request.route]), AT, observed
        )


@pytest.fixture
def clean_main(monkeypatch):
    source = replace(
        inspect_source(Path.cwd()),
        source_commit_sha=SHA,
        source_branch="main",
        clean=True,
    )
    monkeypatch.setattr(
        "atp.kraken_private_qualification.connectivity.inspect_source", lambda root: source
    )
    return source


def run(loader: Loader, transport: FixtureTransport):
    times = iter((AT, AT + timedelta(seconds=5)))
    return qualify_private_connectivity(
        expected_source_sha=SHA,
        source_root=Path.cwd(),
        credential_loader=loader,
        transport=transport,
        clock=lambda: next(times),
    )


def test_private_connectivity_passes_with_exact_three_read_calls(clean_main) -> None:
    loader = Loader()
    transport = FixtureTransport()
    result = run(loader, transport)
    assert result.status is KrakenPrivateConnectivityStatus.PASSED
    assert (
        result.reason_code is KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_CONNECTIVITY_QUALIFIED
    )
    assert result.completed_routes == (
        "/0/private/GetApiKeyInfo",
        "/0/private/Balance",
        "/0/private/OpenOrders",
    )
    assert result.private_network_calls == 3
    assert result.real_economic_calls == 0
    assert result.live == "LIVE_FORBIDDEN"
    assert result.side_effect_performed is False
    assert len(result.evidence_identities) == 4
    assert loader.credential is not None
    with pytest.raises(ValueError, match="KRAKEN_CREDENTIAL_UNAVAILABLE"):
        loader.credential.api_key_bytes()


def test_source_must_be_exact_clean_main_before_credential_acquisition(
    monkeypatch, clean_main
) -> None:
    loader = Loader()
    transport = FixtureTransport()
    monkeypatch.setattr(
        "atp.kraken_private_qualification.connectivity.inspect_source",
        lambda root: replace(clean_main, source_branch="agent/not-main"),
    )
    result = run(loader, transport)
    assert result.status is KrakenPrivateConnectivityStatus.FAILED
    assert result.reason_code is KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID
    assert loader.calls == 0
    assert transport.calls == []
    assert result.private_network_calls == 0


def test_source_change_after_observations_fails_closed(monkeypatch, clean_main) -> None:
    changed = replace(clean_main, source_commit_sha="b" * 40)
    values = iter((clean_main, changed))
    monkeypatch.setattr(
        "atp.kraken_private_qualification.connectivity.inspect_source", lambda root: next(values)
    )
    result = run(Loader(), FixtureTransport())
    assert result.status is KrakenPrivateConnectivityStatus.FAILED
    assert result.reason_code is KrakenPrivateConnectivityReason.KRAKEN_SOURCE_INVALID
    assert result.private_network_calls == 3
    assert result.source_identity is None


@pytest.mark.parametrize(
    ("transport_reason", "result_reason"),
    [
        ("KRAKEN_PRIVATE_TIMEOUT", "KRAKEN_PRIVATE_TIMEOUT"),
        ("KRAKEN_PRIVATE_NETWORK_FAILURE", "KRAKEN_PRIVATE_NETWORK_FAILURE"),
        ("KRAKEN_PRIVATE_HTTP_FAILURE", "KRAKEN_PRIVATE_HTTP_FAILURE"),
        ("KRAKEN_PRIVATE_RESPONSE_TOO_LARGE", "KRAKEN_PRIVATE_RESPONSE_TOO_LARGE"),
    ],
)
def test_transport_failures_are_sanitized_and_never_retried(
    clean_main, transport_reason: str, result_reason: str
) -> None:
    transport = FixtureTransport(failure=transport_reason)
    result = run(Loader(), transport)
    assert result.status is KrakenPrivateConnectivityStatus.FAILED
    assert result.reason_code.value == result_reason
    assert len(transport.calls) == result.private_network_calls == 1


def test_api_key_response_must_bind_to_loaded_key(clean_main) -> None:
    class Mismatch(FixtureTransport):
        def post(self, request, credential, nonce_provider):
            observation = super().post(request, credential, nonce_provider)
            if request.route is KrakenPrivateReadRoute.API_KEY_INFO:
                observation.payload["result"]["apiKey"] = "different-key"
            return observation

    result = run(Loader(), Mismatch())
    assert result.status is KrakenPrivateConnectivityStatus.FAILED
    assert result.reason_code is KrakenPrivateConnectivityReason.KRAKEN_CREDENTIAL_BINDING_MISMATCH
    assert result.private_network_calls == 1


def test_kraken_error_envelopes_are_mapped_without_retry(clean_main) -> None:
    class Rejected(FixtureTransport):
        def __init__(self, error: str) -> None:
            super().__init__()
            self.error = error

        def post(self, request, credential, nonce_provider):
            observation = super().post(request, credential, nonce_provider)
            return replace(observation, payload={"error": [self.error], "result": {}})

    for error, reason in (
        ("EAPI:Invalid key", KrakenPrivateConnectivityReason.KRAKEN_AUTH_REJECTED),
        ("EAPI:Invalid nonce", KrakenPrivateConnectivityReason.KRAKEN_NONCE_REJECTED),
        ("EGeneral:Permission denied", KrakenPrivateConnectivityReason.KRAKEN_PERMISSION_REJECTED),
        ("EService:Unavailable", KrakenPrivateConnectivityReason.KRAKEN_PRIVATE_API_REJECTED),
    ):
        result = run(Loader(), Rejected(error))
        assert result.reason_code is reason
        assert result.private_network_calls == 1


def test_result_contains_no_credential_or_raw_sensitive_metadata(clean_main) -> None:
    result = run(Loader(), FixtureTransport())
    document = json.dumps(encoded(result), sort_keys=True)
    raw = json.dumps(fixture("api-key-info.json"), sort_keys=True)
    for value in (
        "fixture-public-key-never-persisted",
        "fixture-iban-never-persisted",
        "192.0.2.1",
        "atp-read-only",
        "c2VjcmV0",
        "API-Key",
        "API-Sign",
        "nonce",
    ):
        assert value not in document
    assert "fixture-public-key-never-persisted" in raw


def test_connectivity_result_cannot_claim_authority(clean_main) -> None:
    result = run(Loader(), FixtureTransport())
    for changes in (
        {"private_network_calls": 2},
        {"real_economic_calls": 1},
        {"live": "LIVE"},
        {"side_effect_performed": True},
    ):
        with pytest.raises(EvidenceError, match="INVALID_KRAKEN_PRIVATE_CONNECTIVITY_RESULT"):
            replace(result, **changes)
    assert {field.name for field in fields(result)}.isdisjoint(
        {"risk_authorized", "order_authorized", "funding_authorized", "oms_authorized"}
    )


def test_existing_private_offline_evidence_identities_are_unchanged() -> None:
    observed_at = datetime(2026, 9, 28, 12, tzinfo=UTC)
    reference = PrivateCredentialReference(VenueId.KRAKEN, "0" * 32, Environment.TEST)
    capability = parse_api_key_info(fixture("api-key-info.json"), reference, observed_at)
    balances = parse_balances(fixture("balance.json"), reference, capability, observed_at)
    request = account_wide_open_orders_request(reference)
    orders = parse_open_orders(
        fixture("open-orders.json"), reference, capability, request, observed_at
    )
    assert tuple(
        str(item.content_identity) for item in (capability, balances, request, orders)
    ) == (
        "sha256:6565509492283d2e98c0230420c481b61987feeba295e88c94a1d3791be1186f",
        "sha256:718e5229ccd0fcf53f933909ba1baa26780dabe92bbc41ead14ce0cb0acb1b49",
        "sha256:5a8b8f51a9f75f2d6affb9494d00c83aad326f0e06bca01f0d5b369862e0f069",
        "sha256:905ae4f096a54554b8c4902b62f3620dfb286a96a03ec985a2c4c8a176c40987",
    )
