"""Closed V1 invariant catalogue; identities do not depend on pytest names."""

from atp.test_qualification.model import EvidenceType, QualificationCase, QualificationSuite

_DEFINITIONS = (
    (
        "FOUNDATION",
        "foundation",
        EvidenceType.VALIDATION_RUN,
        "python_supported lock_valid lint_passes types_pass tests_pass structure_expected",
    ),
    (
        "SHARED",
        "shared",
        EvidenceType.UNIT_TEST_RESULT,
        "canonical_serialization content_identity typed_identifiers utc environment_contracts",
    ),
    (
        "DATA",
        "data",
        EvidenceType.CONTRACT_TEST_RESULT,
        "valid_as_of_use finality no_gap strict_order available_at "
        "no_rewrite lineage universe_compatibility",
    ),
    (
        "STRATEGY",
        "strategy",
        EvidenceType.CONTRACT_TEST_RESULT,
        "determinism no_future invalid_input_blocks proposal_only",
    ),
    (
        "RISK",
        "risk",
        EvidenceType.CONTRACT_TEST_RESULT,
        "malformed_blocks max_positions market_policy environment_blocks "
        "symbol_binding determinism",
    ),
    (
        "BACKTEST",
        "backtesting",
        EvidenceType.CONTRACT_TEST_RESULT,
        "risk_gate next_open no_current_close no_skip no_future_state end_unfilled determinism",
    ),
    (
        "ACCOUNTING",
        "accounting",
        EvidenceType.CONTRACT_TEST_RESULT,
        "external_quantity decimal duplicate_blocks causal_fills "
        "ledger_integrity policy_locked replay_integrity valuation_integrity equity",
    ),
    (
        "OBS",
        "observability",
        EvidenceType.CONTRACT_TEST_RESULT,
        "event_not_log determinism typed_payload chain duplicate tamper "
        "causation causal_time no_sensitive_payload",
    ),
    (
        "BOUNDARY",
        "boundaries",
        EvidenceType.MODULE_BOUNDARY_RESULT,
        "strategy risk backtesting accounting observability",
    ),
    (
        "SECURITY",
        "security",
        EvidenceType.SECURITY_CHECK_RESULT,
        "no_secret no_withdrawal no_live no_credentials "
        "no_sensitive_payload no_environment_fallback",
    ),
)


def cases_v1() -> tuple[QualificationCase, ...]:
    return tuple(
        QualificationCase(
            case_id=f"Q-{name}-001",
            case_version="1.0",
            title=name,
            invariant_id=f"ATP-V1-{name}",
            required_evidence_types=(evidence_type,),
            required_checks=tuple(checks.split()),
            source_module=module,
        )
        for name, module, evidence_type, checks in _DEFINITIONS
    )


def suite_v1() -> QualificationSuite:
    return QualificationSuite(tuple(sorted(case.case_id for case in cases_v1())))


CASES_V1 = cases_v1()
SUITE_V1 = suite_v1()
