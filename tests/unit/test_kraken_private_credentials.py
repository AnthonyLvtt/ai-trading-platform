from __future__ import annotations

import hashlib

import pytest

import atp.exchange.kraken.private_credentials as credentials_module
from atp.exchange.kraken.private_credentials import (
    KrakenCredentialError,
    load_interactive_credential,
)
from atp.shared.environment import Environment


def test_masked_loader_creates_local_reference_and_cleans_buffers() -> None:
    values = iter(("public-key", "c2VjcmV0"))
    prompts: list[str] = []

    def reader(prompt: str) -> str:
        prompts.append(prompt)
        return next(values)

    credential = load_interactive_credential(reader)
    expected = hashlib.sha256(b"ATP/KRAKEN/PRIVATE-CREDENTIAL/v1\0public-key").hexdigest()[:32]
    assert credential.reference.reference_id == expected
    assert credential.reference.environment is Environment.LOCAL
    assert prompts == [
        "Kraken Spot read-only API key: ",
        "Kraken Spot read-only API secret: ",
    ]
    credential.close()
    with pytest.raises(KrakenCredentialError, match="KRAKEN_CREDENTIAL_UNAVAILABLE"):
        credential.api_key_bytes()
    with pytest.raises(KrakenCredentialError, match="KRAKEN_CREDENTIAL_UNAVAILABLE"):
        credential.secret_buffer()


@pytest.mark.parametrize("values", [("", "c2VjcmV0"), ("key", "not-base64")])
def test_malformed_credentials_fail_with_sanitized_error(values: tuple[str, str]) -> None:
    reader_values = iter(values)
    with pytest.raises(KrakenCredentialError, match="KRAKEN_CREDENTIAL_INVALID") as error:
        load_interactive_credential(lambda _: next(reader_values))
    for value in values:
        if value:
            assert value not in str(error.value)


def test_interrupted_acquisition_fails_without_material() -> None:
    def reader(_: str) -> str:
        raise EOFError

    with pytest.raises(KrakenCredentialError, match="KRAKEN_CREDENTIAL_UNAVAILABLE"):
        load_interactive_credential(reader)


def test_partial_buffer_construction_failure_zeroes_existing_buffer(monkeypatch) -> None:
    created: list[bytearray] = []

    def build(value: str) -> bytearray:
        buffer = bytearray(value, "ascii")
        created.append(buffer)
        return buffer

    monkeypatch.setattr(credentials_module, "_mutable_ascii", build)
    values = iter(("temporary-api-key", "é"))
    with pytest.raises(KrakenCredentialError, match="KRAKEN_CREDENTIAL_UNAVAILABLE") as error:
        load_interactive_credential(lambda _: next(values))
    assert created == [bytearray(b"\0" * len("temporary-api-key"))]
    assert "temporary-api-key" not in str(error.value)
    assert "é" not in str(error.value)
