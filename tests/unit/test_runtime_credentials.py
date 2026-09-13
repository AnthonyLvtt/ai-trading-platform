"""Offline session-attestation regressions; all credentials are synthetic."""

import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from atp.shared.identity import ContentIdentity
from atp.testnet_activation.composition import CredentialCapabilityUnavailable
from atp.testnet_activation.contracts import Reason
from atp.testnet_activation.runtime_credentials import (
    ReferencedEnvironmentCredentialsProvider,
    RuntimeCredentialCapabilityAuthority,
    TestnetCredentialPermissionAttestation,
    TrustedCredentialPermissionAuthority,
    new_credential_reference,
)
from tests.activation_support import activation
from tests.unit.test_release_deployment import inputs

__all__ = ["activation", "inputs"]


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("network forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


NOW = datetime(2026, 1, 1, tzinfo=UTC)
KEY = "offline-only-key"
SECRET = "offline-only-secret_+/=é"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ReferencedEnvironmentCredentialsProvider:
    monkeypatch.setenv("ATP_BINANCE_TESTNET_API_KEY", KEY)
    monkeypatch.setenv("ATP_BINANCE_TESTNET_API_SECRET", SECRET)
    return ReferencedEnvironmentCredentialsProvider(new_credential_reference())


def statement(
    provider: ReferencedEnvironmentCredentialsProvider,
) -> TestnetCredentialPermissionAttestation:
    return TestnetCredentialPermissionAttestation(
        credential_reference_id=provider.reference.credential_reference_id,
        trading_capability_confirmed=True,
        withdrawal_capability_absent=True,
        verified_by_role="synthetic-operator",
        verified_at=NOW,
        valid_until=NOW + timedelta(hours=1),
    )


def authority(
    provider: ReferencedEnvironmentCredentialsProvider,
    attestation: TestnetCredentialPermissionAttestation,
    *,
    at: datetime = NOW,
) -> RuntimeCredentialCapabilityAuthority:
    return RuntimeCredentialCapabilityAuthority(
        provider,
        attestation,
        TrustedCredentialPermissionAuthority(attestation.content_identity),
        lambda: at,
    )


def test_matching_evidence_is_safe(
    provider: ReferencedEnvironmentCredentialsProvider, caplog: pytest.LogCaptureFixture
) -> None:
    attestation = statement(provider)
    runtime = authority(provider, attestation)
    evidence = runtime.attest(provider.reference.content_identity)
    assert evidence.credentials_present
    assert evidence.trading_capability_confirmed
    assert evidence.withdrawal_capability_absent
    assert evidence.permission_attestation_identity == attestation.content_identity
    assert evidence.credential_reference_id == provider.reference.credential_reference_id
    assert runtime.attest(provider.reference.content_identity) == evidence
    for output in (repr(evidence), repr(provider), repr(runtime), caplog.text):
        assert KEY not in output and SECRET not in output


@pytest.mark.parametrize("missing", ["API_KEY", "API_SECRET"])
def test_missing_secret(
    provider: ReferencedEnvironmentCredentialsProvider,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.delenv("ATP_BINANCE_TESTNET_" + missing)
    with pytest.raises(CredentialCapabilityUnavailable) as error:
        authority(provider, statement(provider)).attest(provider.reference.content_identity)
    assert error.value.reason == Reason.CREDENTIAL_CAPABILITY_INVALID
    assert SECRET not in str(error.value)


def test_no_attestation(provider: ReferencedEnvironmentCredentialsProvider) -> None:
    runtime = RuntimeCredentialCapabilityAuthority(
        provider, None, TrustedCredentialPermissionAuthority(), lambda: NOW
    )
    with pytest.raises(CredentialCapabilityUnavailable):
        runtime.attest(provider.reference.content_identity)


@pytest.mark.parametrize("pin", [None, ContentIdentity.from_text("different")])
def test_role_is_not_authority(
    provider: ReferencedEnvironmentCredentialsProvider, pin: ContentIdentity | None
) -> None:
    attestation = replace(statement(provider), verified_by_role="CTO")
    runtime = RuntimeCredentialCapabilityAuthority(
        provider, attestation, TrustedCredentialPermissionAuthority(pin), lambda: NOW
    )
    with pytest.raises(CredentialCapabilityUnavailable) as error:
        runtime.attest(provider.reference.content_identity)
    assert error.value.reason == Reason.CREDENTIAL_CAPABILITY_UNTRUSTED


@pytest.mark.parametrize(
    "field,value",
    [
        ("trading_capability_confirmed", False),
        ("withdrawal_capability_absent", False),
        ("environment", "LIVE"),
        ("attestation_source", "CREDENTIAL_PRESENCE"),
        ("valid_until", NOW),
        ("valid_until", NOW + timedelta(hours=24, seconds=1)),
        ("verified_at", NOW + timedelta(seconds=1)),
    ],
)
def test_invalid_facts(
    provider: ReferencedEnvironmentCredentialsProvider, field: str, value: object
) -> None:
    attestation = replace(statement(provider), **{field: value})
    with pytest.raises(CredentialCapabilityUnavailable):
        authority(provider, attestation).attest(provider.reference.content_identity)


def test_expiry_rechecked(provider: ReferencedEnvironmentCredentialsProvider) -> None:
    attestation = statement(provider)
    with pytest.raises(CredentialCapabilityUnavailable):
        authority(provider, attestation, at=attestation.valid_until).attest(
            provider.reference.content_identity
        )


def test_other_provider(provider: ReferencedEnvironmentCredentialsProvider) -> None:
    other = ReferencedEnvironmentCredentialsProvider(new_credential_reference())
    with pytest.raises(CredentialCapabilityUnavailable) as error:
        authority(other, statement(provider)).attest(other.reference.content_identity)
    assert error.value.reason == Reason.CREDENTIAL_REFERENCE_MISMATCH
    with pytest.raises(CredentialCapabilityUnavailable) as error:
        authority(provider, statement(provider)).attest(other.reference.content_identity)
    assert error.value.reason == Reason.CREDENTIAL_REFERENCE_MISMATCH


def test_pre_pin_tampering(provider: ReferencedEnvironmentCredentialsProvider) -> None:
    attestation = statement(provider)
    object.__setattr__(attestation, "verified_by_role", "tampered")
    with pytest.raises(CredentialCapabilityUnavailable):
        authority(provider, attestation).attest(provider.reference.content_identity)


def test_reference_independent_of_secrets(
    provider: ReferencedEnvironmentCredentialsProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = provider.reference.content_identity
    monkeypatch.setenv("ATP_BINANCE_TESTNET_API_SECRET", "different-synthetic-secret")
    assert provider.reference.content_identity == identity
    assert new_credential_reference() != provider.reference


def test_activation_consumes_runtime_attestation(provider, activation) -> None:
    from atp.testnet_activation.composition import validate_activation
    from atp.testnet_activation.contracts import context_error

    at = activation["at"]
    attestation = replace(
        statement(provider), verified_at=at, valid_until=at + timedelta(seconds=30)
    )
    request = activation | {
        "credential_source_identity": provider.reference.content_identity,
        "credential_authority": authority(provider, attestation, at=at),
    }
    result = validate_activation(**request)
    assert result.reason_code == Reason.TESTNET_ACTIVATION_ALLOWED
    assert result.context.validity_end == attestation.valid_until
    assert context_error(result.context, at=attestation.valid_until) is not None
    request["credential_authority"] = RuntimeCredentialCapabilityAuthority(
        provider, attestation, TrustedCredentialPermissionAuthority(), lambda: at
    )
    assert validate_activation(**request).reason_code == Reason.CREDENTIAL_CAPABILITY_UNTRUSTED
