"""Source-bound, network-free Kraken private read-only qualification."""

from dataclasses import dataclass
from pathlib import Path

from atp.exchange.contracts import VenueId
from atp.exchange.kraken.private import KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST
from atp.exchange.private_contracts import (
    AccountBalanceEvidence,
    AccountOpenOrdersEvidence,
    PrivateCredentialCapabilityEvidence,
    PrivateCredentialReference,
)
from atp.exchange.read_only import verify_record
from atp.kraken_private_qualification.model import (
    KrakenPrivateQualificationReason,
    KrakenPrivateQualificationResult,
    KrakenPrivateQualificationStatus,
)
from atp.release_deployment.model import ReleaseError, SourceTree
from atp.release_deployment.source import inspect_source
from atp.shared.identity import ContentIdentity


@dataclass(frozen=True, slots=True)
class _Evaluation:
    status: KrakenPrivateQualificationStatus
    reason: KrakenPrivateQualificationReason
    reference_identity: ContentIdentity | None
    evidence_identities: tuple[ContentIdentity, ...]


def _evaluate(
    reference: object,
    capability: object,
    balances: object,
    open_orders: object,
) -> _Evaluation:
    records = (
        (reference, PrivateCredentialReference),
        (capability, PrivateCredentialCapabilityEvidence),
        (balances, AccountBalanceEvidence),
        (open_orders, AccountOpenOrdersEvidence),
    )
    if any(not verify_record(value, expected) for value, expected in records) or not isinstance(
        reference, PrivateCredentialReference
    ):
        return _Evaluation(
            KrakenPrivateQualificationStatus.FAILED,
            KrakenPrivateQualificationReason.KRAKEN_PRIVATE_EVIDENCE_INVALID,
            None,
            (),
        )
    assert isinstance(capability, PrivateCredentialCapabilityEvidence)
    assert isinstance(balances, AccountBalanceEvidence)
    assert isinstance(open_orders, AccountOpenOrdersEvidence)
    evidence = (capability, balances, open_orders)
    if (
        reference.venue is not VenueId.KRAKEN
        or any(item.venue is not VenueId.KRAKEN for item in evidence)
        or any(
            item.credential_reference_identity != reference.content_identity for item in evidence
        )
        or balances.capability_identity != capability.content_identity
        or open_orders.capability_identity != capability.content_identity
        or balances.scope != "ACCOUNT_WIDE_DEFAULT_WALLET"
        or open_orders.scope != "ACCOUNT_WIDE"
        or open_orders.complete is not True
        or abs((balances.observed_at - capability.observed_at).total_seconds()) > 60
        or abs((open_orders.observed_at - capability.observed_at).total_seconds()) > 60
    ):
        return _Evaluation(
            KrakenPrivateQualificationStatus.FAILED,
            KrakenPrivateQualificationReason.KRAKEN_PRIVATE_EVIDENCE_INVALID,
            reference.content_identity,
            tuple(item.content_identity for item in evidence),
        )
    return _Evaluation(
        KrakenPrivateQualificationStatus.PASSED,
        KrakenPrivateQualificationReason.KRAKEN_PRIVATE_READ_QUALIFIED,
        reference.content_identity,
        tuple(item.content_identity for item in evidence),
    )


def _bind(evaluation: _Evaluation, source: SourceTree | None) -> KrakenPrivateQualificationResult:
    return KrakenPrivateQualificationResult(
        status=evaluation.status,
        reason_code=evaluation.reason,
        venue=VenueId.KRAKEN,
        credential_reference_identity=evaluation.reference_identity,
        evidence_identities=evaluation.evidence_identities,
        route_allowlist=tuple(sorted(KRAKEN_PRIVATE_READ_ROUTE_ALLOWLIST)),
        source_commit_sha=None if source is None else source.source_commit_sha,
        source_tree_sha=None if source is None else source.git_tree_sha,
        repository_identity=None if source is None else source.repository_identity,
        source_identity=None if source is None else source.content_identity,
    )


def qualify_private_offline(
    reference: object,
    capability: object,
    balances: object,
    open_orders: object,
    *,
    source_root: Path | None = None,
) -> KrakenPrivateQualificationResult:
    root = Path.cwd() if source_root is None else source_root
    try:
        before = inspect_source(root)
        if not before.clean:
            raise ValueError("dirty source")
        evaluation = _evaluate(reference, capability, balances, open_orders)
        after = inspect_source(root)
        if before != after or not after.clean:
            raise ValueError("source changed")
        return _bind(evaluation, before)
    except (ReleaseError, OSError, ValueError):
        return _bind(
            _Evaluation(
                KrakenPrivateQualificationStatus.FAILED,
                KrakenPrivateQualificationReason.KRAKEN_SOURCE_INVALID,
                None,
                (),
            ),
            None,
        )
