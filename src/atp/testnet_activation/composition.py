"""Explicit trusted composition. No default authorities, credentials, I/O or grant issuer."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from atp.release_deployment.engine import inspect_bundle
from atp.release_deployment.model import ReleaseBundle, sensitive
from atp.shared.identity import ContentIdentity
from atp.testnet_activation.contracts import (
    ActivationPolicy,
    ActivationResult,
    Reason,
    RuntimeAuthorizationContext,
    TestnetActivationGrant,
    TrustedActivationGrantEvidence,
    TrustedCredentialCapabilityEvidence,
    _seal_context,
    encode,
    valid,
)
from atp.testnet_qualification.engine import inspect_qualification
from atp.testnet_qualification.model import Status, TestnetQualificationSuiteResult


class TrustedActivationAuthority(ABC):
    """Installed by trusted composition only; no implementation ships in runtime."""

    @property
    @abstractmethod
    def accepted_grant_identity(self) -> ContentIdentity: ...

    @abstractmethod
    def attest(self, grant: TestnetActivationGrant) -> TrustedActivationGrantEvidence: ...


class CredentialCapabilityAuthority(ABC):
    """Receives an opaque source identity, never secret material."""

    @abstractmethod
    def attest(
        self, credential_source_identity: ContentIdentity
    ) -> TrustedCredentialCapabilityEvidence: ...


def validate_activation(
    grant: object = None,
    *,
    source_commit_sha: object = None,
    repository_identity: object = None,
    release: object = None,
    wheel: object = None,
    tq: object = None,
    tq_evidence: object = None,
    credential_source_identity: object = None,
    symbol: object = None,
    order_type: object = None,
    at: object = None,
    grant_authority: TrustedActivationAuthority | None = None,
    credential_authority: CredentialCapabilityAuthority | None = None,
    environment: object = "TESTNET",
    policy: object = None,
) -> ActivationResult:
    def block(reason: Reason) -> ActivationResult:
        return ActivationResult(reason)

    if type(environment) is str and environment == "LIVE":
        return block(Reason.LIVE_FORBIDDEN)
    if type(environment) is not str or environment != "TESTNET":
        return block(Reason.INVALID_ACTIVATION_INPUT)
    p = ActivationPolicy() if policy is None else policy
    if not valid(p, ActivationPolicy):
        return block(Reason.INVALID_ACTIVATION_INPUT)
    if grant is None:
        return block(Reason.ACTIVATION_GRANT_REQUIRED)
    if not valid(grant, TestnetActivationGrant):
        return block(Reason.ACTIVATION_GRANT_INVALID)
    assert isinstance(grant, TestnetActivationGrant) and isinstance(p, ActivationPolicy)
    if not isinstance(grant_authority, TrustedActivationAuthority):
        return block(Reason.ACTIVATION_GRANT_UNTRUSTED)
    # Authority implementations are trusted dependencies; programming defects propagate.
    if grant_authority.accepted_grant_identity != grant.content_identity:
        return block(Reason.ACTIVATION_GRANT_UNTRUSTED)
    trusted = grant_authority.attest(grant)
    if not valid(trusted, TrustedActivationGrantEvidence):
        return block(Reason.ACTIVATION_GRANT_UNTRUSTED)
    if (
        trusted.grant_content_identity != grant.content_identity
        or trusted.policy_identity != p.content_identity
        or trusted.source_commit_sha != grant.source_commit_sha
        or trusted.repository_identity != grant.repository_identity
        or trusted.release_identity != grant.release_candidate_identity
        or trusted.tq_identity != grant.testnet_qualification_identity
        or trusted.authority_type != "TRUSTED_COMPOSITION"
        or not trusted.authority_reference
        or sensitive(encode(trusted))
    ):
        return block(Reason.ACTIVATION_GRANT_UNTRUSTED)
    if type(at) is not datetime or at.tzinfo is not UTC:
        return block(Reason.INVALID_ACTIVATION_INPUT)
    if at < grant.validity_start:
        return block(Reason.ACTIVATION_GRANT_NOT_YET_VALID)
    if at >= grant.validity_end:
        return block(Reason.ACTIVATION_GRANT_EXPIRED)
    if type(source_commit_sha) is not str or type(repository_identity) is not ContentIdentity:
        return block(Reason.INVALID_ACTIVATION_INPUT)
    try:
        encode(repository_identity)
    except ValueError:
        return block(Reason.INVALID_ACTIVATION_INPUT)
    if (
        source_commit_sha != grant.source_commit_sha
        or repository_identity != grant.repository_identity
    ):
        return block(Reason.ACTIVATION_SOURCE_MISMATCH)
    if type(symbol) is not str or symbol not in grant.allowed_symbols:
        return block(Reason.SYMBOL_NOT_AUTHORIZED)
    if type(order_type) is not str or order_type not in grant.allowed_order_types:
        return block(Reason.ORDER_TYPE_NOT_AUTHORIZED)
    if release is None:
        return block(Reason.RELEASE_BINDING_REQUIRED)
    if inspect_bundle(release, wheel) is not None:
        return block(Reason.RELEASE_BINDING_MISMATCH)
    assert isinstance(release, ReleaseBundle)
    if (
        release.candidate.content_identity != grant.release_candidate_identity
        or release.manifest.content_identity != grant.release_manifest_identity
        or release.source.source_commit_sha != source_commit_sha
        or release.source.repository_identity != repository_identity
    ):
        return block(Reason.RELEASE_BINDING_MISMATCH)
    if tq is None:
        return block(Reason.TESTNET_QUALIFICATION_REQUIRED)
    if not inspect_qualification(
        tq,
        tq_evidence,
        source_commit_sha=source_commit_sha,
        repository_identity=repository_identity,
    ):
        return block(Reason.TESTNET_QUALIFICATION_INVALID)
    assert isinstance(tq, TestnetQualificationSuiteResult)
    reconciliation = next((c for c in tq.cases if c.case_id == "TQ-RECONCILE-001"), None)
    if reconciliation is None or reconciliation.status is not Status.PASSED:
        return block(Reason.RECONCILIATION_NOT_READY)
    if tq.status is not Status.PASSED or tq.side_effects_performed is not False:
        return block(Reason.TESTNET_QUALIFICATION_INVALID)
    if tq.content_identity != grant.testnet_qualification_identity:
        return block(Reason.TESTNET_QUALIFICATION_MISMATCH)
    if type(credential_source_identity) is not ContentIdentity:
        return block(Reason.CREDENTIAL_CAPABILITY_REQUIRED)
    if not isinstance(credential_authority, CredentialCapabilityAuthority):
        return block(Reason.CREDENTIAL_CAPABILITY_INVALID)
    capabilities = credential_authority.attest(credential_source_identity)
    if not valid(capabilities, TrustedCredentialCapabilityEvidence):
        return block(Reason.CREDENTIAL_CAPABILITY_INVALID)
    if capabilities.withdrawal_capability_absent is False:
        return block(Reason.WITHDRAWAL_CAPABILITY_FORBIDDEN)
    if (
        capabilities.environment != "TESTNET"
        or capabilities.authority_type != "TRUSTED_COMPOSITION_ATTESTATION"
        or capabilities.credential_source_identity != credential_source_identity
        or not capabilities.authority_reference
        or sensitive(encode(capabilities))
        or any(
            v is not True
            for v in (
                capabilities.credentials_present,
                capabilities.trading_capability_confirmed,
                capabilities.withdrawal_capability_absent,
            )
        )
    ):
        return block(Reason.CREDENTIAL_CAPABILITY_INVALID)
    assert isinstance(symbol, str) and isinstance(order_type, str)
    context = RuntimeAuthorizationContext(
        "TESTNET",
        grant.content_identity,
        tq.content_identity,
        release.candidate.content_identity,
        release.manifest.content_identity,
        capabilities.content_identity,
        reconciliation.content_identity,
        trusted.content_identity,
        capabilities.content_identity,
        credential_source_identity,
        grant.source_commit_sha,
        grant.repository_identity,
        release.qualification.result.content_identity,
        symbol,
        order_type,
        grant.validity_start,
        grant.validity_end,
        p.content_identity,
    )
    return ActivationResult(Reason.TESTNET_ACTIVATION_ALLOWED, _seal_context(context))
