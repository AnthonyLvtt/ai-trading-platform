"""Ephemeral local credential acquisition for Kraken private qualification."""

from __future__ import annotations

import base64
import getpass
import hashlib
from collections.abc import Callable

from atp.exchange.contracts import VenueId
from atp.exchange.private_contracts import PrivateCredentialReference
from atp.shared.environment import Environment


class KrakenCredentialError(ValueError):
    """Sanitized credential acquisition failure."""


class EphemeralKrakenCredential:
    """Process-local credential material that is never an evidence record."""

    __slots__ = ("_api_key", "_closed", "_secret", "reference")

    def __init__(self, api_key: bytearray, secret: bytearray) -> None:
        if not api_key or len(api_key) > 256 or not secret or len(secret) > 512:
            raise KrakenCredentialError("KRAKEN_CREDENTIAL_INVALID")
        try:
            bytes(api_key).decode("ascii")
            base64.b64decode(secret, validate=True)
        except (UnicodeError, ValueError, TypeError):
            raise KrakenCredentialError("KRAKEN_CREDENTIAL_INVALID") from None
        reference_id = hashlib.sha256(
            b"ATP/KRAKEN/PRIVATE-CREDENTIAL/v1\0" + bytes(api_key)
        ).hexdigest()[:32]
        self.reference = PrivateCredentialReference(
            VenueId.KRAKEN,
            reference_id,
            Environment.LOCAL,
        )
        self._api_key = api_key
        self._secret = secret
        self._closed = False

    def api_key_bytes(self) -> bytes:
        if self._closed:
            raise KrakenCredentialError("KRAKEN_CREDENTIAL_UNAVAILABLE")
        return bytes(self._api_key)

    def secret_buffer(self) -> bytearray:
        if self._closed:
            raise KrakenCredentialError("KRAKEN_CREDENTIAL_UNAVAILABLE")
        return self._secret

    def close(self) -> None:
        if self._closed:
            return
        for value in (self._api_key, self._secret):
            value[:] = b"\0" * len(value)
        self._closed = True

    def __enter__(self) -> EphemeralKrakenCredential:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


CredentialReader = Callable[[str], str]


def _mutable_ascii(value: str) -> bytearray:
    return bytearray(value, "ascii")


def _zero_buffer(value: bytearray | None) -> None:
    if value is not None:
        value[:] = b"\0" * len(value)


def load_interactive_credential(
    reader: CredentialReader = getpass.getpass,
) -> EphemeralKrakenCredential:
    """Read both credential values from masked local terminal prompts."""
    api_key: bytearray | None = None
    secret: bytearray | None = None
    try:
        api_key_text = reader("Kraken Spot read-only API key: ")
        secret_text = reader("Kraken Spot read-only API secret: ")
        api_key = _mutable_ascii(api_key_text)
        secret = _mutable_ascii(secret_text)
    except (EOFError, KeyboardInterrupt, UnicodeError, OSError):
        _zero_buffer(api_key)
        _zero_buffer(secret)
        raise KrakenCredentialError("KRAKEN_CREDENTIAL_UNAVAILABLE") from None
    finally:
        if "api_key_text" in locals():
            api_key_text = ""
        if "secret_text" in locals():
            secret_text = ""
    if api_key is None or secret is None:
        raise KrakenCredentialError("KRAKEN_CREDENTIAL_UNAVAILABLE")
    try:
        return EphemeralKrakenCredential(api_key, secret)
    except KrakenCredentialError:
        _zero_buffer(api_key)
        _zero_buffer(secret)
        raise
