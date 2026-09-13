"""Injected composition authorities; no production implementation or serialized authority."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from weakref import WeakValueDictionary, finalize

from atp.exchange.read_only import EvidenceRecord, verify_record
from atp.first_testnet_order.model import (
    FirstOrderPolicy,
    FirstTestnetOrderAuthorization,
    Reason,
    TrustedFirstOrderAuthorizationEvidence,
)
from atp.shared.identity import ContentIdentity


class TrustedFirstOrderAuthority(ABC):
    @property
    @abstractmethod
    def accepted_authorization_identity(self) -> ContentIdentity: ...

    @abstractmethod
    def attest(
        self, authorization: FirstTestnetOrderAuthorization
    ) -> TrustedFirstOrderAuthorizationEvidence: ...


@dataclass(frozen=True, slots=True, weakref_slot=True)
class FirstOrderReceipt(EvidenceRecord):
    authorization_identity: ContentIdentity
    trusted_evidence_identity: ContentIdentity


_receipts: WeakValueDictionary[int, FirstOrderReceipt] = WeakValueDictionary()
_pins: dict[int, ContentIdentity] = {}


def trust_first_order(
    authorization: object, authority: object = None
) -> FirstOrderReceipt | Reason:
    if authorization is None:
        return Reason.FIRST_ORDER_AUTHORIZATION_REQUIRED
    if not verify_record(authorization, FirstTestnetOrderAuthorization):
        return Reason.FIRST_ORDER_AUTHORIZATION_INVALID
    assert isinstance(authorization, FirstTestnetOrderAuthorization)
    if (
        not isinstance(authority, TrustedFirstOrderAuthority)
        or authority.accepted_authorization_identity != authorization.content_identity
    ):
        return Reason.FIRST_ORDER_AUTHORIZATION_UNTRUSTED
    evidence = authority.attest(authorization)
    if (
        not verify_record(evidence, TrustedFirstOrderAuthorizationEvidence)
        or evidence.authorization_identity != authorization.content_identity
        or evidence.policy_identity != FirstOrderPolicy().content_identity
        or evidence.authority_type != "TRUSTED_COMPOSITION"
        or not evidence.authority_reference
    ):
        return Reason.FIRST_ORDER_AUTHORIZATION_UNTRUSTED
    receipt = FirstOrderReceipt(authorization.content_identity, evidence.content_identity)
    _receipts[id(receipt)] = receipt
    _pins[id(receipt)] = receipt.content_identity
    finalize(receipt, _pins.pop, id(receipt), None)
    return receipt


def receipt_valid(receipt: object, authorization: FirstTestnetOrderAuthorization) -> bool:
    return (
        verify_record(receipt, FirstOrderReceipt)
        and isinstance(receipt, FirstOrderReceipt)
        and _receipts.get(id(receipt)) is receipt
        and _pins.get(id(receipt)) == receipt.content_identity
        and receipt.authorization_identity == authorization.content_identity
    )
