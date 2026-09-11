"""Immutable Exchange contracts. No credentials belong in these records."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from atp.risk.model import RiskDecision
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity


class Status(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    UNCERTAIN = "UNCERTAIN"


class Reason(StrEnum):
    EXCHANGE_OK = "EXCHANGE_OK"
    LIVE_FORBIDDEN = "LIVE_FORBIDDEN"
    TESTNET_NOT_AUTHORIZED = "TESTNET_NOT_AUTHORIZED"
    INVALID_EXCHANGE_ORDER = "INVALID_EXCHANGE_ORDER"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    RISK_AUTHORIZATION_INVALID = "RISK_AUTHORIZATION_INVALID"
    CREDENTIALS_UNAVAILABLE = "CREDENTIALS_UNAVAILABLE"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TRANSPORT_UNAVAILABLE = "TRANSPORT_UNAVAILABLE"
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    SUBMISSION_OUTCOME_UNKNOWN = "SUBMISSION_OUTCOME_UNKNOWN"
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"
    MALFORMED_EXCHANGE_RESPONSE = "MALFORMED_EXCHANGE_RESPONSE"
    EXCHANGE_POLICY_MISMATCH = "EXCHANGE_POLICY_MISMATCH"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    UNSUPPORTED_ORDER_TYPE = "UNSUPPORTED_ORDER_TYPE"
    UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is Decimal:
        if not value.is_finite():
            raise ValidationError("invalid decimal")
        return decimal_text(value)
    if type(value) is datetime:
        if value.tzinfo is not UTC:
            raise ValidationError("UTC required")
        return value.isoformat()
    if type(value) is ContentIdentity:
        value.__post_init__()
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: canonical(getattr(value, f.name))
            for f in fields(value)
            if f.name != "content_identity"
        }
    raise ValidationError("invalid exchange record")


def identity(value: object) -> ContentIdentity:
    return ContentIdentity.from_canonical(canonical(value))


def typed(value: object, expected: object, depth: int = 0) -> bool:
    """Validate against trusted contract annotations before invoking domain code."""
    if depth > 25:
        return False
    if get_origin(expected) is UnionType:
        return any(typed(value, t, depth + 1) for t in get_args(expected))
    if get_origin(expected) is tuple:
        args = get_args(expected)
        return (
            type(value) is tuple
            and len(args) == 2
            and args[1] is Ellipsis
            and all(typed(v, args[0], depth + 1) for v in value)
        )
    if type(value) is not expected:
        return False
    if type(value) is Decimal:
        return value.is_finite()
    if type(value) is datetime:
        return value.tzinfo is UTC
    if is_dataclass(value) and not isinstance(value, type):
        hints = get_type_hints(type(value))
        return all(typed(getattr(value, f.name), hints[f.name], depth + 1) for f in fields(value))
    return True


def valid(value: object, expected: type[object]) -> bool:
    try:
        if not typed(value, expected):
            return False
        _verify(value)
        return True
    except (AttributeError, TypeError, ValueError, ValidationError):
        return False


def _verify(value: object) -> None:
    if type(value) is tuple:
        for item in value:
            _verify(item)
    elif is_dataclass(value) and not isinstance(value, type):
        for f in fields(value):
            _verify(getattr(value, f.name))
        post = getattr(value, "__post_init__", None)
        if post is not None:
            post()


@dataclass(frozen=True, slots=True)
class ExchangePolicy:
    policy_id: str = "ATP_EXCHANGE_TESTNET_V1"
    version: str = "1.0"
    venue: str = "BINANCE"
    market: str = "SPOT"
    environment: str = "TESTNET"
    live_allowed: bool = False
    withdrawals_allowed: bool = False
    margin_allowed: bool = False
    futures_allowed: bool = False
    short_allowed: bool = False
    leverage_allowed: bool = False
    retry_mode: str = "SAFE_ONLY"
    reconciliation_required_on_uncertain_submission: bool = True

    def __post_init__(self) -> None:
        for f in fields(self):
            if (
                type(getattr(self, f.name)) is not type(f.default)
                or getattr(self, f.name) != f.default
            ):
                raise ValidationError("Exchange policy must be exact V1")

    @property
    def content_identity(self) -> ContentIdentity:
        return identity(self)


@dataclass(frozen=True, slots=True)
class UpstreamOrderProof:
    """External quantity/intent fact, bound to a Risk decision; no sizing."""

    symbol: str
    side: Side
    quantity: Decimal
    risk_decision_id: str
    risk_decision_identity: ContentIdentity
    strategy_evaluation_identity: ContentIdentity
    environment: str
    created_at: datetime
    content_identity: ContentIdentity

    @property
    def upstream_order_id(self) -> str:
        return f"upstream-order:{self.content_identity}"

    def __post_init__(self) -> None:
        if self.content_identity != identity(self):
            raise ValidationError("upstream order identity mismatch")


@dataclass(frozen=True, slots=True)
class ExchangeOrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    order_type: str
    quantity: Decimal
    risk_decision_id: str
    risk_decision_identity: ContentIdentity
    upstream_order_id: str
    upstream_order_identity: ContentIdentity
    environment: str
    created_at: datetime
    policy_id: str
    policy_version: str
    content_identity: ContentIdentity

    def __post_init__(self) -> None:
        if self.content_identity != identity(self):
            raise ValidationError("exchange order identity mismatch")


@dataclass(frozen=True, slots=True)
class TestnetExecutionAuthorization:
    environment: str
    ops_readiness_identity: ContentIdentity
    qualification_identity: ContentIdentity
    observability_identity: ContentIdentity
    issued_for_policy: ContentIdentity
    content_identity: ContentIdentity

    def __post_init__(self) -> None:
        if self.content_identity != identity(self):
            raise ValidationError("authorization identity mismatch")


@dataclass(frozen=True, slots=True)
class ExchangeSubmissionResult:
    status: Status
    reason_code: Reason
    venue: str
    environment: str
    client_order_id: str | None
    exchange_order_id: str | None
    symbol: str | None
    side: Side | None
    submitted_at: datetime | None
    exchange_event_time: datetime | None
    request_identity: ContentIdentity | None
    upstream_order_identity: ContentIdentity | None
    risk_decision_identity: ContentIdentity | None
    policy_identity: ContentIdentity
    content_identity: ContentIdentity

    def __post_init__(self) -> None:
        if self.content_identity != identity(self):
            raise ValidationError("submission identity mismatch")


def client_order_id(upstream_identity: ContentIdentity) -> str:
    if not valid(upstream_identity, ContentIdentity):
        raise ValidationError("invalid upstream identity")
    return f"atp-{upstream_identity.digest[:32]}"


def request_from_proof(proof: UpstreamOrderProof) -> ExchangeOrderRequest:
    if not valid(proof, UpstreamOrderProof):
        raise ValidationError("invalid upstream proof")
    values = dict(
        client_order_id=client_order_id(proof.content_identity),
        symbol=proof.symbol,
        side=proof.side,
        order_type="MARKET",
        quantity=proof.quantity,
        risk_decision_id=proof.risk_decision_id,
        risk_decision_identity=proof.risk_decision_identity,
        upstream_order_id=proof.upstream_order_id,
        upstream_order_identity=proof.content_identity,
        environment=proof.environment,
        created_at=proof.created_at,
        policy_id=ExchangePolicy().policy_id,
        policy_version=ExchangePolicy().version,
    )
    return ExchangeOrderRequest(
        **values,  # type: ignore[arg-type]
        content_identity=ContentIdentity.from_canonical(
            {k: canonical(v) for k, v in values.items()}
        ),
    )


def risk_is_valid(risk: object) -> bool:
    return valid(risk, RiskDecision)


@dataclass(frozen=True, slots=True)
class ExchangeConnectivityResult:
    available: bool
    reason_code: Reason
    policy_identity: ContentIdentity

    @property
    def content_identity(self) -> ContentIdentity:
        return identity(self)
