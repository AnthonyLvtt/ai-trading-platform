"""Explicit session composition for manually verified Testnet permissions.

No default pin, permission probe, secret persistence, or order capability.
The operator, not this module, establishes the out-of-band permission facts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from atp.exchange.transport import CredentialMaterial, EnvironmentCredentialsProvider
from atp.release_deployment.model import sensitive
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.composition import (
    CredentialCapabilityAuthority,
    CredentialCapabilityUnavailable,
)
from atp.testnet_activation.contracts import (
    Reason,
    Record,
    TrustedCredentialCapabilityEvidence,
    encode,
    valid,
)

MANUAL_SOURCE = "MANUAL_OUT_OF_BAND_TESTNET_PERMISSION_ATTESTATION"
PROVIDER_TYPE = "PROCESS_ENVIRONMENT"


@dataclass(frozen=True, slots=True)
class CredentialReference(Record):
    credential_reference_id: str
    provider_type: str = PROVIDER_TYPE
    environment: str = "TESTNET"


def new_credential_reference() -> CredentialReference:
    """Generate an opaque local identifier without consulting credentials."""
    return CredentialReference(credential_reference_id=uuid4().hex)


@dataclass(frozen=True, slots=True)
class TestnetCredentialPermissionAttestation(Record):
    __test__ = False
    credential_reference_id: str
    trading_capability_confirmed: bool
    withdrawal_capability_absent: bool
    verified_by_role: str
    verified_at: datetime
    valid_until: datetime
    schema_version: str = "1.0"
    environment: str = "TESTNET"
    attestation_source: str = MANUAL_SOURCE


def _reference_valid(reference: object) -> bool:
    return (
        valid(reference, CredentialReference)
        and isinstance(reference, CredentialReference)
        and reference.environment == "TESTNET"
        and reference.provider_type == PROVIDER_TYPE
        and bool(reference.credential_reference_id)
        and not sensitive(encode(reference))
    )


class ReferencedEnvironmentCredentialsProvider:
    """Existing process-environment loader bound to a non-secret reference."""

    def __init__(self, reference: CredentialReference) -> None:
        if not _reference_valid(reference):
            raise CredentialCapabilityUnavailable(Reason.CREDENTIAL_CAPABILITY_INVALID)
        self.reference = reference
        self._provider = EnvironmentCredentialsProvider()

    def load(self) -> CredentialMaterial | None:
        return self._provider.load()

    def credentials_present(self) -> bool:
        material = self.load()
        return material is not None and all(
            type(value) is str and bool(value) for value in (material.api_key, material.api_secret)
        )

    def __repr__(self) -> str:
        return "ReferencedEnvironmentCredentialsProvider(<opaque>)"


class TrustedCredentialPermissionAuthority:
    """Exact pin supplied by trusted session composition, never by role inference."""

    def __init__(self, accepted_attestation_identity: ContentIdentity | None = None) -> None:
        self._accepted = accepted_attestation_identity

    def verify(self, attestation: TestnetCredentialPermissionAttestation) -> None:
        if not valid(attestation, TestnetCredentialPermissionAttestation):
            raise CredentialCapabilityUnavailable(Reason.CREDENTIAL_CAPABILITY_INVALID)
        if (
            type(self._accepted) is not ContentIdentity
            or self._accepted != attestation.content_identity
        ):
            raise CredentialCapabilityUnavailable(Reason.CREDENTIAL_CAPABILITY_UNTRUSTED)


class RuntimeCredentialCapabilityAuthority(CredentialCapabilityAuthority):
    """Combine provider presence and independent pinned manual attestation.

    Instantiate explicitly for a session. Rechecks integrity and expiry on every
    attest call; constructing an attestation alone never establishes authority.
    """

    def __init__(
        self,
        provider: ReferencedEnvironmentCredentialsProvider,
        attestation: object,
        permission_authority: TrustedCredentialPermissionAuthority,
        clock: Callable[[], datetime],
    ) -> None:
        self._provider = provider
        self._attestation = attestation
        self._authority = permission_authority
        self._clock = clock

    def attest(
        self, credential_source_identity: ContentIdentity
    ) -> TrustedCredentialCapabilityEvidence:
        invalid = Reason.CREDENTIAL_CAPABILITY_INVALID
        provider = self._provider
        attestation = self._attestation
        if (
            type(provider) is not ReferencedEnvironmentCredentialsProvider
            or not _reference_valid(provider.reference)
            or not valid(attestation, TestnetCredentialPermissionAttestation)
            or not isinstance(attestation, TestnetCredentialPermissionAttestation)
        ):
            raise CredentialCapabilityUnavailable(invalid)
        reference = provider.reference
        if (
            type(credential_source_identity) is not ContentIdentity
            or credential_source_identity != reference.content_identity
            or attestation.credential_reference_id != reference.credential_reference_id
        ):
            raise CredentialCapabilityUnavailable(Reason.CREDENTIAL_REFERENCE_MISMATCH)
        if type(self._authority) is not TrustedCredentialPermissionAuthority:
            raise CredentialCapabilityUnavailable(Reason.CREDENTIAL_CAPABILITY_UNTRUSTED)
        self._authority.verify(attestation)
        at = self._clock()
        if (
            type(at) is not datetime
            or at.tzinfo is not UTC
            or attestation.schema_version != "1.0"
            or attestation.environment != "TESTNET"
            or attestation.attestation_source != MANUAL_SOURCE
            or attestation.trading_capability_confirmed is not True
            or attestation.withdrawal_capability_absent is not True
            or not attestation.verified_by_role
            or not timedelta(0)
            < attestation.valid_until - attestation.verified_at
            <= timedelta(hours=24)
            or not attestation.verified_at <= at < attestation.valid_until
            or sensitive(encode(attestation))
            or not provider.credentials_present()
        ):
            raise CredentialCapabilityUnavailable(invalid)
        return TrustedCredentialCapabilityEvidence(
            credentials_present=True,
            trading_capability_confirmed=True,
            withdrawal_capability_absent=True,
            credential_source_identity=reference.content_identity,
            credential_reference_id=reference.credential_reference_id,
            permission_attestation_identity=attestation.content_identity,
            permission_verified_at=attestation.verified_at,
            permission_valid_until=attestation.valid_until,
            authority_reference=str(attestation.content_identity),
        )

    def __repr__(self) -> str:
        return "RuntimeCredentialCapabilityAuthority(<session-bound>)"
