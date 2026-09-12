"""Closed offline qualification catalogue; no result grants execution authority."""

from dataclasses import dataclass, fields
from enum import StrEnum

from atp.exchange.read_only import EvidenceError, EvidenceRecord
from atp.exchange.shadow import ExchangeShadowProjection
from atp.shared.identity import ContentIdentity


class Status(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class Reason(StrEnum):
    TESTNET_QUALIFICATION_PASSED = "TESTNET_QUALIFICATION_PASSED"
    ADAPTER_CONTRACT_INVALID = "ADAPTER_CONTRACT_INVALID"
    TESTNET_CONFIGURATION_INVALID = "TESTNET_CONFIGURATION_INVALID"
    TESTNET_ENVIRONMENT_MISMATCH = "TESTNET_ENVIRONMENT_MISMATCH"
    RISK_APPROVAL_REQUIRED = "RISK_APPROVAL_REQUIRED"
    SIZING_EVIDENCE_REQUIRED = "SIZING_EVIDENCE_REQUIRED"
    SYMBOL_FILTER_INCOMPATIBLE = "SYMBOL_FILTER_INCOMPATIBLE"
    UNSUPPORTED_SYMBOL_FILTER = "UNSUPPORTED_SYMBOL_FILTER"
    REQUEST_SERIALIZATION_INVALID = "REQUEST_SERIALIZATION_INVALID"
    RESPONSE_MAPPING_INVALID = "RESPONSE_MAPPING_INVALID"
    IDEMPOTENCY_INVARIANT_VIOLATED = "IDEMPOTENCY_INVARIANT_VIOLATED"
    AMBIGUOUS_SUBMISSION_STATE = "AMBIGUOUS_SUBMISSION_STATE"
    RECONCILIATION_CAPABILITY_MISSING = "RECONCILIATION_CAPABILITY_MISSING"
    CONNECTIVITY_EVIDENCE_MISSING = "CONNECTIVITY_EVIDENCE_MISSING"
    CONNECTIVITY_EVIDENCE_INVALID = "CONNECTIVITY_EVIDENCE_INVALID"
    CREDENTIAL_MATERIAL_DETECTED = "CREDENTIAL_MATERIAL_DETECTED"
    SIDE_EFFECT_DETECTED = "SIDE_EFFECT_DETECTED"
    LIVE_FORBIDDEN = "LIVE_FORBIDDEN"
    TESTNET_RUNTIME_NOT_AUTHORIZED = "TESTNET_RUNTIME_NOT_AUTHORIZED"
    INVALID_TESTNET_QUALIFICATION_INPUT = "INVALID_TESTNET_QUALIFICATION_INPUT"


@dataclass(frozen=True, slots=True)
class TestnetQualificationPolicy(EvidenceRecord):
    __test__ = False
    policy_id: str = "ATP_TESTNET_QUALIFICATION_V1"
    policy_version: str = "1.0"
    live_allowed: bool = False
    testnet_order_submission_allowed: bool = False
    withdrawal_allowed: bool = False
    shadow_only: bool = True
    signed_write_requests_allowed: bool = False
    external_side_effects_allowed: bool = False

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name != "content_identity" and (
                type(getattr(self, f.name)) is not type(f.default)
                or getattr(self, f.name) != f.default
            ):
                raise EvidenceError("INVALID_POLICY")
        EvidenceRecord.__post_init__(self)


@dataclass(frozen=True, slots=True)
class ShadowIntent(EvidenceRecord):
    symbol: str
    side: str
    strategy_identity: ContentIdentity
    risk_identity: ContentIdentity
    upstream_identity: ContentIdentity
    projection: ExchangeShadowProjection
    filters_identity: ContentIdentity
    price_identity: ContentIdentity | None
    policy_identity: ContentIdentity
    source_risk_environment: str = "TEST"
    qualification_target: str = "TESTNET"
    submission_authorized: bool = False

    @property
    def client_intent_id(self) -> str:
        return f"shadow:{self.content_identity}"


@dataclass(frozen=True, slots=True)
class ShadowEvaluationResult(EvidenceRecord):
    status: Status
    reason_code: Reason
    intent: ShadowIntent | None = None
    side_effect_performed: bool = False


@dataclass(frozen=True, slots=True)
class TestnetQualificationCase(EvidenceRecord):
    __test__ = False
    case_id: str
    probes: tuple[str, ...]
    failure_reason: Reason


# Stable explicit probe mapping, never inferred from a global pytest exit code.
CASES = (
    TestnetQualificationCase(
        "TQ-ADAPTER-001",
        ("test_tq_adapter_binding", "test_tq_runtime_matrices_unchanged"),
        Reason.TESTNET_CONFIGURATION_INVALID,
    ),
    TestnetQualificationCase(
        "TQ-SERIALIZE-001", ("test_tq_serialization",), Reason.REQUEST_SERIALIZATION_INVALID
    ),
    TestnetQualificationCase(
        "TQ-PARSE-001", ("test_tq_error_mapping",), Reason.RESPONSE_MAPPING_INVALID
    ),
    TestnetQualificationCase(
        "TQ-IDEMPOTENCY-001", ("test_tq_idempotency",), Reason.IDEMPOTENCY_INVARIANT_VIOLATED
    ),
    TestnetQualificationCase(
        "TQ-FILTERS-001",
        (
            "test_tq_filters",
            "test_tq_price_evidence",
            "test_tq_min_notional_last_price_and_precision",
        ),
        Reason.SYMBOL_FILTER_INCOMPATIBLE,
    ),
    TestnetQualificationCase(
        "TQ-FAILCLOSED-001",
        ("test_tq_malformed", "test_tq_unknown_no_retry"),
        Reason.ADAPTER_CONTRACT_INVALID,
    ),
    TestnetQualificationCase(
        "TQ-RECONCILE-001",
        ("test_tq_reconciliation", "test_tq_read_only_query_contract"),
        Reason.RECONCILIATION_CAPABILITY_MISSING,
    ),
    TestnetQualificationCase(
        "TQ-SHADOW-001",
        (
            "test_tq_vertical_shadow",
            "test_tq_risk_required",
            "test_tq_valid_nonapproval_and_environment_mismatch",
        ),
        Reason.RISK_APPROVAL_REQUIRED,
    ),
    TestnetQualificationCase(
        "TQ-SECURITY-001", ("test_tq_security",), Reason.CREDENTIAL_MATERIAL_DETECTED
    ),
    TestnetQualificationCase(
        "TQ-BOUNDARY-001",
        (
            "test_tq_boundaries",
            "test_tq_suite_integrity_and_aggregation",
            "test_tq_policy_mutation_and_tampered_filters",
            "test_tq_result_source_binding",
        ),
        Reason.SIDE_EFFECT_DETECTED,
    ),
)


@dataclass(frozen=True, slots=True)
class ProbeObservation(EvidenceRecord):
    probe_id: str
    node_id: str
    setup: str
    call: str
    teardown: str
    subject_identities: tuple[ContentIdentity, ...]


@dataclass(frozen=True, slots=True)
class ExchangeContractEvidence(EvidenceRecord):
    case_id: str
    source_commit_sha: str
    repository_identity: ContentIdentity
    adapter_identity: ContentIdentity
    policy_identity: ContentIdentity
    observations: tuple[ProbeObservation, ...]
    level: str = "OFFLINE_CONTRACT"
    side_effects_performed: bool = False


@dataclass(frozen=True, slots=True)
class TestnetCapabilityEvidence(EvidenceRecord):
    __test__ = False
    source_commit_sha: str
    repository_identity: ContentIdentity
    contracts: tuple[ExchangeContractEvidence, ...]


@dataclass(frozen=True, slots=True)
class TestnetQualificationResult(EvidenceRecord):
    __test__ = False
    case_id: str
    status: Status
    reason_code: Reason
    evidence_identity: ContentIdentity | None


@dataclass(frozen=True, slots=True)
class TestnetQualificationSuiteResult(EvidenceRecord):
    __test__ = False
    source_commit_sha: str | None
    repository_identity: ContentIdentity | None
    adapter_identity: ContentIdentity
    policy_identity: ContentIdentity
    required_cases: tuple[str, ...]
    cases: tuple[TestnetQualificationResult, ...]
    status: Status
    reason_code: Reason
    side_effects_performed: bool = False
