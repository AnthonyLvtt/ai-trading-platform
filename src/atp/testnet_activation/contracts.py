"""Activation contracts and process-local receipts; no domain engines or I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import UnionType
from typing import get_args, get_origin, get_type_hints
from weakref import WeakValueDictionary, finalize

from atp.shared.identity import ContentIdentity


class Reason(StrEnum):
    TESTNET_ACTIVATION_ALLOWED = "TESTNET_ACTIVATION_ALLOWED"
    ACTIVATION_GRANT_REQUIRED = "ACTIVATION_GRANT_REQUIRED"
    ACTIVATION_GRANT_INVALID = "ACTIVATION_GRANT_INVALID"
    ACTIVATION_GRANT_UNTRUSTED = "ACTIVATION_GRANT_UNTRUSTED"
    ACTIVATION_GRANT_EXPIRED = "ACTIVATION_GRANT_EXPIRED"
    ACTIVATION_GRANT_NOT_YET_VALID = "ACTIVATION_GRANT_NOT_YET_VALID"
    ACTIVATION_SOURCE_MISMATCH = "ACTIVATION_SOURCE_MISMATCH"
    TESTNET_QUALIFICATION_REQUIRED = "TESTNET_QUALIFICATION_REQUIRED"
    TESTNET_QUALIFICATION_INVALID = "TESTNET_QUALIFICATION_INVALID"
    TESTNET_QUALIFICATION_MISMATCH = "TESTNET_QUALIFICATION_MISMATCH"
    RELEASE_BINDING_REQUIRED = "RELEASE_BINDING_REQUIRED"
    RELEASE_BINDING_MISMATCH = "RELEASE_BINDING_MISMATCH"
    CREDENTIAL_CAPABILITY_REQUIRED = "CREDENTIAL_CAPABILITY_REQUIRED"
    CREDENTIAL_CAPABILITY_INVALID = "CREDENTIAL_CAPABILITY_INVALID"
    RECONCILIATION_NOT_READY = "RECONCILIATION_NOT_READY"
    SYMBOL_NOT_AUTHORIZED = "SYMBOL_NOT_AUTHORIZED"
    ORDER_TYPE_NOT_AUTHORIZED = "ORDER_TYPE_NOT_AUTHORIZED"
    RISK_NOT_AUTHORIZED = "RISK_NOT_AUTHORIZED"
    OPS_NOT_READY = "OPS_NOT_READY"
    RELEASE_NOT_PROMOTED = "RELEASE_NOT_PROMOTED"
    TESTNET_RUNTIME_BLOCKED = "TESTNET_RUNTIME_BLOCKED"
    LIVE_FORBIDDEN = "LIVE_FORBIDDEN"
    WITHDRAWAL_CAPABILITY_FORBIDDEN = "WITHDRAWAL_CAPABILITY_FORBIDDEN"
    INVALID_ACTIVATION_INPUT = "INVALID_ACTIVATION_INPUT"


def encode(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is ContentIdentity:
        if (
            type(value.algorithm) is not str
            or value.algorithm != "sha256"
            or type(value.digest) is not str
            or not re.fullmatch("[0-9a-f]{64}", value.digest)
        ):
            raise ValueError("Invalid identity")
        return str(value)
    if type(value) is datetime and value.tzinfo is UTC:
        return value.isoformat()
    if type(value) is tuple:
        return [encode(v) for v in value]
    if isinstance(value, Record):
        return {
            f.name: encode(getattr(value, f.name))
            for f in fields(value)
            if f.name != "content_identity"
        }
    raise ValueError("Invalid activation input")


def _typed(value: object, expected: object) -> bool:
    if get_origin(expected) is UnionType:
        return any(_typed(value, t) for t in get_args(expected))
    if get_origin(expected) is tuple:
        return type(value) is tuple and all(_typed(v, get_args(expected)[0]) for v in value)
    if type(value) is not expected:
        return False
    if is_dataclass(value) and not isinstance(value, type):
        hints = get_type_hints(type(value))
        return all(_typed(getattr(value, f.name), hints[f.name]) for f in fields(value))
    return True


@dataclass(frozen=True, slots=True, kw_only=True, weakref_slot=True)
class Record:
    content_identity: ContentIdentity = field(init=False)

    def __post_init__(self) -> None:
        expected = ContentIdentity.from_canonical(encode(self))
        if hasattr(self, "content_identity") and self.content_identity != expected:
            raise ValueError("Activation identity mismatch")
        object.__setattr__(self, "content_identity", expected)


def valid(value: object, expected: type[Record]) -> bool:
    try:
        if not _typed(value, expected) or not isinstance(value, Record):
            return False
        value.__post_init__()
        return value.content_identity == ContentIdentity.from_canonical(encode(value))
    except (AttributeError, TypeError, ValueError, RecursionError):
        return False


@dataclass(frozen=True, slots=True)
class ActivationPolicy(Record):
    policy_id: str = "ATP_TESTNET_ACTIVATION_V1"
    policy_version: str = "1.0"
    live_allowed: bool = False
    withdrawal_allowed: bool = False
    implicit_activation_allowed: bool = False
    fallback_to_live_allowed: bool = False
    activation_grant_required: bool = True
    tq_qualification_required: bool = True
    release_binding_required: bool = True
    credentials_required_at_runtime: bool = True
    reconciliation_required: bool = True
    first_testnet_order_authorized: bool = False

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.init and (
                type(getattr(self, f.name)) is not type(f.default)
                or getattr(self, f.name) != f.default
            ):
                raise ValueError("Activation policy must be exact V1")
        Record.__post_init__(self)


@dataclass(frozen=True, slots=True)
class TestnetActivationGrant(Record):
    __test__ = False
    source_commit_sha: str
    repository_identity: ContentIdentity
    release_candidate_identity: ContentIdentity
    release_manifest_identity: ContentIdentity
    testnet_qualification_identity: ContentIdentity
    allowed_symbols: tuple[str, ...]
    allowed_order_types: tuple[str, ...]
    validity_start: datetime
    validity_end: datetime
    issued_by_role: str
    schema_version: str = "1.0"
    policy_id: str = "ATP_TESTNET_ACTIVATION_V1"
    policy_version: str = "1.0"
    allowed_environment: str = "TESTNET"
    max_active_positions: int = 1

    @property
    def grant_id(self) -> str:
        return "testnet-grant:" + str(self.content_identity)

    def __post_init__(self) -> None:
        if (
            self.schema_version,
            self.policy_id,
            self.policy_version,
            self.allowed_environment,
            self.max_active_positions,
        ) != ("1.0", "ATP_TESTNET_ACTIVATION_V1", "1.0", "TESTNET", 1):
            raise ValueError("Invalid grant policy")
        if not re.fullmatch("[0-9a-f]{40}", self.source_commit_sha):
            raise ValueError("Invalid source")
        for values in (self.allowed_symbols, self.allowed_order_types):
            if (
                not values
                or tuple(sorted(set(values))) != values
                or any(not re.fullmatch("[A-Z0-9]{2,30}", v) or v in ("ALL", "ANY") for v in values)
            ):
                raise ValueError("Invalid grant scope")
        if (
            self.validity_start.tzinfo is not UTC
            or self.validity_end.tzinfo is not UTC
            or self.validity_end <= self.validity_start
        ):
            raise ValueError("Invalid grant window")
        if self.issued_by_role != "CTO":
            raise ValueError("Invalid declarative role")
        Record.__post_init__(self)


@dataclass(frozen=True, slots=True)
class TrustedActivationGrantEvidence(Record):
    grant_content_identity: ContentIdentity
    policy_identity: ContentIdentity
    source_commit_sha: str
    repository_identity: ContentIdentity
    release_identity: ContentIdentity
    tq_identity: ContentIdentity
    authority_reference: str
    authority_type: str = "TRUSTED_COMPOSITION"


@dataclass(frozen=True, slots=True)
class TrustedCredentialCapabilityEvidence(Record):
    credentials_present: bool | None
    trading_capability_confirmed: bool | None
    withdrawal_capability_absent: bool | None
    credential_source_identity: ContentIdentity
    authority_reference: str
    environment: str = "TESTNET"
    authority_type: str = "TRUSTED_COMPOSITION_ATTESTATION"


@dataclass(frozen=True, slots=True)
class RuntimeAuthorizationContext(Record):
    environment: str
    activation_grant_identity: ContentIdentity
    tq_identity: ContentIdentity
    release_identity: ContentIdentity
    release_manifest_identity: ContentIdentity
    credential_capability_identity: ContentIdentity
    reconciliation_evidence_identity: ContentIdentity
    trusted_activation_evidence_identity: ContentIdentity
    trusted_credential_capability_identity: ContentIdentity
    credential_source_identity: ContentIdentity
    source_commit_sha: str
    repository_identity: ContentIdentity
    qualification_identity: ContentIdentity
    symbol: str
    order_type: str
    validity_start: datetime
    validity_end: datetime
    policy_identity: ContentIdentity


# Receipts are not serialized authority. Only the owning composition module issues them.
# Weak references prevent retaining a completed runtime/session indefinitely.
_contexts: WeakValueDictionary[int, RuntimeAuthorizationContext] = WeakValueDictionary()
_pins: dict[int, ContentIdentity] = {}


def _seal_context(context: RuntimeAuthorizationContext) -> RuntimeAuthorizationContext:
    _contexts[id(context)] = context
    _pins[id(context)] = context.content_identity
    finalize(context, _pins.pop, id(context), None)
    return context


def context_error(
    context: object, at: object, *, symbol: object = None, order_type: object = None
) -> Reason | None:
    if context is None:
        return Reason.ACTIVATION_GRANT_REQUIRED
    if not valid(context, RuntimeAuthorizationContext):
        return Reason.INVALID_ACTIVATION_INPUT
    assert isinstance(context, RuntimeAuthorizationContext)
    if context.environment == "LIVE":
        return Reason.LIVE_FORBIDDEN
    if (
        _contexts.get(id(context)) is not context
        or _pins.get(id(context)) != context.content_identity
    ):
        return Reason.ACTIVATION_GRANT_UNTRUSTED
    if (
        context.environment != "TESTNET"
        or context.policy_identity != ActivationPolicy().content_identity
    ):
        return Reason.INVALID_ACTIVATION_INPUT
    if type(at) is not datetime or at.tzinfo is not UTC:
        return Reason.INVALID_ACTIVATION_INPUT
    if at < context.validity_start:
        return Reason.ACTIVATION_GRANT_NOT_YET_VALID
    if at >= context.validity_end:
        return Reason.ACTIVATION_GRANT_EXPIRED
    if symbol is not None and (type(symbol) is not str or symbol != context.symbol):
        return Reason.SYMBOL_NOT_AUTHORIZED
    if order_type is not None and (type(order_type) is not str or order_type != context.order_type):
        return Reason.ORDER_TYPE_NOT_AUTHORIZED
    return None


@dataclass(frozen=True, slots=True)
class ActivationResult(Record):
    reason_code: Reason
    context: RuntimeAuthorizationContext | None = None
    transport_call_count: int = 0

    @property
    def status(self) -> str:
        return "ALLOWED" if self.reason_code is Reason.TESTNET_ACTIVATION_ALLOWED else "BLOCKED"


def inspect_context_reference(identity: object) -> RuntimeAuthorizationContext | None:
    """Inspect a historical receipt without interpreting it as fresh authorization."""
    if type(identity) is not ContentIdentity:
        return None
    for context in list(_contexts.values()):
        if (
            context.content_identity == identity
            and context_error(context, context.validity_start) is None
        ):
            return context
    return None
