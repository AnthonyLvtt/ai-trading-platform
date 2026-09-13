"""Deterministic single-use authorization and read-only result contracts."""

import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from atp.exchange.model import client_order_id
from atp.exchange.read_only import EvidenceRecord, encoded
from atp.shared.identity import ContentIdentity


class Reason(StrEnum):
    FIRST_ORDER_AUTHORIZATION_REQUIRED = "FIRST_ORDER_AUTHORIZATION_REQUIRED"
    FIRST_ORDER_AUTHORIZATION_UNTRUSTED = "FIRST_ORDER_AUTHORIZATION_UNTRUSTED"
    FIRST_ORDER_AUTHORIZATION_INVALID = "FIRST_ORDER_AUTHORIZATION_INVALID"
    FIRST_ORDER_AUTHORIZATION_EXPIRED = "FIRST_ORDER_AUTHORIZATION_EXPIRED"
    FIRST_ORDER_AUTHORIZATION_MISMATCH = "FIRST_ORDER_AUTHORIZATION_MISMATCH"
    FIRST_ORDER_ALREADY_CONSUMED = "FIRST_ORDER_ALREADY_CONSUMED"
    FIRST_ORDER_NOT_READY = "FIRST_ORDER_NOT_READY"
    QUANTITY_NOT_AUTHORIZED = "QUANTITY_NOT_AUTHORIZED"
    NOTIONAL_LIMIT_EXCEEDED = "NOTIONAL_LIMIT_EXCEEDED"
    SUBMISSION_LEDGER_UNAVAILABLE = "SUBMISSION_LEDGER_UNAVAILABLE"
    SUBMISSION_STATE_UNKNOWN = "SUBMISSION_STATE_UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    TESTNET_ENDPOINT_MISMATCH = "TESTNET_ENDPOINT_MISMATCH"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SYMBOL_FILTER_EVIDENCE_STALE = "SYMBOL_FILTER_EVIDENCE_STALE"
    INVALID_FILTER_EVIDENCE = "INVALID_FILTER_EVIDENCE"
    PRICE_EVIDENCE_STALE = "PRICE_EVIDENCE_STALE"
    TIME_EVIDENCE_INVALID = "TIME_EVIDENCE_INVALID"
    LIVE_FORBIDDEN = "LIVE_FORBIDDEN"
    RECONCILED = "RECONCILED"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"


class SubmissionState(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    RECONCILED = "RECONCILED"


@dataclass(frozen=True, slots=True)
class FirstOrderPolicy(EvidenceRecord):
    policy_id: str = "ATP_FIRST_TESTNET_ORDER_V1"
    policy_version: str = "1.0"
    max_submissions: int = 1
    live_allowed: bool = False
    withdrawal_allowed: bool = False
    automatic_retry_allowed: bool = False
    automatic_resubmit_allowed: bool = False
    background_execution_allowed: bool = False
    manual_execution_required: bool = True
    reconciliation_required: bool = True
    max_filter_age_seconds: int = 900
    max_price_age_seconds: int = 10
    max_clock_skew_seconds: int = 5

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.init and (
                type(getattr(self, f.name)) is not type(f.default)
                or getattr(self, f.name) != f.default
            ):
                raise ValueError("First order policy must be exact V1")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class FirstTestnetOrderAuthorization(EvidenceRecord):
    activation_grant_identity: ContentIdentity
    runtime_authorization_context_identity: ContentIdentity
    source_commit_sha: str
    release_identity: ContentIdentity
    tq_identity: ContentIdentity
    upstream_proof_identity: ContentIdentity
    submission_ledger_identity: ContentIdentity
    symbol: str
    quantity: Decimal
    max_quote_notional: Decimal
    valid_from: datetime
    valid_until: datetime
    schema_version: str = "1.0"
    policy_id: str = "ATP_FIRST_TESTNET_ORDER_V1"
    policy_version: str = "1.0"
    environment: str = "TESTNET"
    side: str = "BUY"
    order_type: str = "MARKET"
    max_submissions: int = 1
    authorization_facts_identity: ContentIdentity = field(init=False)
    client_order_id: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.schema_version,
            self.policy_id,
            self.policy_version,
            self.environment,
            self.side,
            self.order_type,
            self.max_submissions,
        ) != ("1.0", "ATP_FIRST_TESTNET_ORDER_V1", "1.0", "TESTNET", "BUY", "MARKET", 1):
            raise ValueError("Invalid first order scope")
        if (
            type(self.max_submissions) is not int
            or not re.fullmatch("[0-9a-f]{40}", self.source_commit_sha)
            or not re.fullmatch("[A-Z0-9]{2,30}", self.symbol)
            or self.symbol in ("ALL", "ANY")
        ):
            raise ValueError("Invalid first order binding")
        for value in (self.quantity, self.max_quote_notional):
            if type(value) is not Decimal or not value.is_finite() or value <= 0:
                raise ValueError("Invalid first order amount")
        if (
            self.valid_from.tzinfo is not UTC
            or self.valid_until.tzinfo is not UTC
            or self.valid_until <= self.valid_from
        ):
            raise ValueError("Invalid first order window")
        facts = ContentIdentity.from_canonical(
            {f.name: encoded(getattr(self, f.name)) for f in fields(self) if f.init}
        )
        client = client_order_id(facts)
        for name, expected in (
            ("authorization_facts_identity", facts),
            ("client_order_id", client),
        ):
            if hasattr(self, name) and getattr(self, name) != expected:
                raise ValueError("First order identity mismatch")
            object.__setattr__(self, name, expected)
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class TrustedFirstOrderAuthorizationEvidence(EvidenceRecord):
    authorization_identity: ContentIdentity
    policy_identity: ContentIdentity
    authority_reference: str
    authority_type: str = "TRUSTED_COMPOSITION"


@dataclass(frozen=True, slots=True)
class FirstOrderResult(EvidenceRecord):
    reason_code: Reason
    state: SubmissionState = SubmissionState.NOT_ATTEMPTED
    authorization_identity: ContentIdentity | None = None
    request_identity: ContentIdentity | None = None
    response_identity: ContentIdentity | None = None
    exchange_order_id: str | None = None
    exchange_status: str | None = None
    acknowledgment_time: datetime | None = None
    transport_call_count: int = 0

    @property
    def status(self) -> str:
        if self.reason_code is Reason.READY_TO_SUBMIT:
            return "READY_TO_SUBMIT"
        if self.state is SubmissionState.UNKNOWN:
            return "UNKNOWN"
        if self.state in (SubmissionState.ACKNOWLEDGED, SubmissionState.RECONCILED):
            return self.state.value
        return "BLOCKED"
