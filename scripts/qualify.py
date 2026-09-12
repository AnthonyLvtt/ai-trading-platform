"""Local explicit evidence collection. Run with `uv run python scripts/qualify.py`.

No raw logs, durations, wall clock, or environment variables enter the bundle.
This script is a test producer, outside the pure qualification runtime.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from atp.release_deployment.source import inspect_source
from atp.shared.serialization import canonical_json_bytes
from atp.test_qualification import (
    CASES_V1,
    SUITE_V1,
    QualificationSubject,
    build_evidence,
    evaluate_suite,
)

# Explicit criterion-to-probe mapping; pytest node names are source references only.
PROBES = {
    "SHARED": {
        "canonical_serialization": "canonical_serialization_is_stable_across_mapping_order",
        "content_identity": "content_identity_is_deterministic",
        "typed_identifiers": "typed_identifiers_remain_distinct_across_concepts",
        "utc": "non_utc_timestamp_is_rejected",
        "environment_contracts": "unknown_environment_refused live_environment_not_enabled",
    },
    "DATA": {
        "valid_as_of_use": "late_invalidation_preserves_validation_as_of_use",
        "finality": "historical_view_rejects_provisional_point_without_explicit_permission",
        "no_gap": "consumer_rejects_non_clean_gap_status_without_explicit_permission",
        "strict_order": "duplicate_event_time_is_blocked_without_silent_selection",
        "available_at": "historical_view_hides_data_not_yet_available",
        "no_rewrite": "backfill_rejects_rewritten_historical_availability",
        "lineage": "lineage_identity_is_reproducible",
        "universe_compatibility": (
            "historical_view_rejects_future_universe unlinked_universe_is_blocked"
        ),
    },
    "STRATEGY": {
        "determinism": "identical_input_is_deterministic",
        "no_future": "future_data_is_not_visible",
        "invalid_input_blocks": (
            "duplicate_event_time_is_blocked_without_silent_selection "
            "incompatible_data_contract_is_blocked"
        ),
        "proposal_only": "strategy_has_no_order_or_quantity_contract",
    },
    "RISK": {
        "malformed_blocks": (
            "malformed_market_runtime_values_are_blocked_deterministically "
            "unknown_or_incoherent_portfolio_is_blocked"
        ),
        "max_positions": "long_entry_with_any_open_position_is_rejected",
        "market_policy": "known_prohibited_market_context_is_rejected",
        "environment_blocks": "unknown_or_inactive_environment_is_blocked",
        "symbol_binding": "strategy_and_market_symbol_mismatch_is_blocked",
        "determinism": "same_input_produces_same_decision",
    },
    "BACKTEST": {
        "risk_gate": "no_action_and_non_approved_risk_never_create_orders",
        "next_open": "long_entry_fills_only_at_next_bar_open_deterministically",
        "no_current_close": "long_entry_fills_only_at_next_bar_open_deterministically",
        "no_skip": (
            "non_final_next_bar_blocks_without_using_later_bar "
            "invalid_next_bar_open_blocks_without_searching_later"
        ),
        "no_future_state": "delayed_fill_state_cannot_leak_into_earlier_following_evaluation",
        "end_unfilled": "end_of_replay_leaves_approved_order_unfilled",
        "determinism": "long_entry_fills_only_at_next_bar_open_deterministically",
    },
    "ACCOUNTING": {
        "external_quantity": "invalid_quantity_fails_closed wrong_exit_quantity_is_blocked",
        "decimal": "buy_entry_records_cash_position_and_immutable_ledger",
        "duplicate_blocks": "duplicate_fill_is_blocked_without_silent_deduplication",
        "causal_fills": "non_causal_execution_sequence_is_blocked",
        "ledger_integrity": "well_typed_tampered_replay_state_fails_closed_during_valuation",
        "policy_locked": (
            "non_normative_accounting_policy_is_rejected "
            "engine_rejects_policy_mutated_after_construction"
        ),
        "replay_integrity": "well_typed_tampered_replay_state_fails_closed_during_valuation",
        "valuation_integrity": "real_slice_valuation_identity_rejects_tampering",
        "equity": "open_position_requires_causal_admissible_mark_and_equity_invariant",
    },
    "OBS": {
        "event_not_log": "diagnostic_record_is_separate_and_uses_explicit_time",
        "determinism": "same_event_has_same_identity_and_identifier",
        "typed_payload": (
            "correct_strategy_fields_with_wrong_value_types_are_blocked "
            "correct_order_fields_with_wrong_value_types_are_blocked"
        ),
        "chain": "journal_append_chain_is_immutable_and_deterministic",
        "duplicate": "duplicate_event_is_blocked_without_deduplication",
        "tamper": "tampered_event_is_detected modified_chain_link_is_detected",
        "causation": "correlation_and_causation_mismatch_are_blocked",
        "causal_time": (
            "backtest_event_ignores_late_snapshot_creation_time "
            "full_vertical_slice_produces_a_verifiable_audit_chain"
        ),
        "no_sensitive_payload": "sensitive_payload_is_blocked_not_redacted",
    },
    "BOUNDARY": {
        "strategy": "strategy_and_risk_have_no_route_to_forbidden_authorities",
        "risk": "strategy_and_risk_have_no_route_to_forbidden_authorities",
        "backtesting": "backtesting_has_no_forbidden_authority_imports",
        "accounting": (
            "accounting_has_no_forbidden_authority_imports "
            "accounting_models_expose_no_order_or_fill_factory"
        ),
        "observability": (
            "observability_has_no_forbidden_authority_or_infrastructure_imports "
            "qualification_has_no_business_or_network_calls"
        ),
    },
    "SECURITY": {
        "no_secret": "secret_payload_blocks_without_echoing_secret",
        "no_withdrawal": "no_withdrawal_or_live_activation_capability",
        "no_live": "live_environment_not_enabled no_withdrawal_or_live_activation_capability",
        "no_credentials": "live_credentials_forbidden_in_standard_tests",
        "no_sensitive_payload": "sensitive_payload_is_blocked_not_redacted",
        "no_environment_fallback": (
            "unknown_or_inactive_environment_is_blocked environment_is_explicit_no_live_fallback"
        ),
    },
}


class Reports:
    def __init__(self):
        self.phases = {}

    def pytest_runtest_logreport(self, report):
        self.phases.setdefault(report.nodeid, {})[report.when] = report.outcome


def collect(*, output_path=None):
    root = Path(__file__).resolve().parents[1]
    if Path.cwd() != root:
        raise SystemExit("Run from the repository root")
    source_before = inspect_source(root)
    reports = Reports()
    exit_code = pytest.main(
        ["tests/unit", "tests/contract", "tests/qualification", "-q"], plugins=[reports]
    )
    command_codes = {}
    for name, args in {
        "lock_valid": ["uv", "lock", "--check"],
        "lint_passes": ["uv", "run", "ruff", "check", "src", "tests", "scripts"],
        "types_pass": ["uv", "run", "mypy", "src"],
    }.items():
        command_codes[name] = subprocess.run(args, capture_output=True, check=False).returncode
    source_after = inspect_source(root)
    if source_before != source_after:
        raise SystemExit("QUALIFICATION_SOURCE_MISMATCH")
    repository_identity = source_after.repository_identity
    subjects, evidence = [], []
    for case in CASES_V1:
        observations, references = {}, {}
        family = case.case_id.split("-")[1]
        for check in case.required_checks:
            if family == "FOUNDATION":
                good = {
                    "python_supported": sys.version_info[:2] == (3, 12),
                    "lock_valid": command_codes["lock_valid"] == 0,
                    "lint_passes": command_codes["lint_passes"] == 0,
                    "types_pass": command_codes["types_pass"] == 0,
                    "tests_pass": exit_code == 0,
                    "structure_expected": all(
                        Path(p).exists()
                        for p in (
                            "src/atp",
                            "tests/unit",
                            "tests/contract",
                            "tests/qualification",
                            "pyproject.toml",
                            "uv.lock",
                            ".github/workflows/quality.yml",
                        )
                    ),
                }[check]
                reproducible = exit_code in (0, 1) and all(
                    code in (0, 1) for code in command_codes.values()
                )
                references[check] = {
                    "python": list(sys.version_info[:2]),
                    "commands": command_codes,
                    "pytest_exit": int(exit_code),
                }
            else:
                names = PROBES[family][check].split()
                selected = {
                    name: {
                        node: phases
                        for node, phases in reports.phases.items()
                        if node.split("::")[-1].split("[")[0] == "test_" + name
                    }
                    for name in names
                }
                phases = [ph for group in selected.values() for ph in group.values()]
                reproducible = all(selected.values()) and all(
                    set(ph) == {"setup", "call", "teardown"}
                    and ph["setup"] == "passed"
                    and ph["teardown"] == "passed"
                    and ph["call"] in ("passed", "failed")
                    for ph in phases
                )
                good = reproducible and all(ph["call"] == "passed" for ph in phases)
                references[check] = selected
            observations[check] = {
                "case_id": case.case_id,
                "check_id": check,
                "satisfied": good,
                "reproducible": reproducible,
                "applicable": True,
            }
        subject = QualificationSubject(
            case.case_id,
            case.source_module,
            "local-probes-v1",
            canonical_json_bytes(
                {
                    "observations": observations,
                    "references": references,
                    "repository_identity": str(repository_identity),
                    "source_commit_sha": source_after.source_commit_sha,
                }
            ),
        )
        subjects.append(subject)
        for check, observation in observations.items():
            built = build_evidence(
                evidence_id=f"{case.case_id}:{check}",
                case=case,
                check_id=check,
                subject=subject,
                payload={k: observation[k] for k in ("satisfied", "applicable", "reproducible")}
                | {"observation_id": check},
            )
            if built.evidence is None:
                raise SystemExit(built.reason_code.value)
            evidence.append(built.evidence)
    result = evaluate_suite(SUITE_V1, CASES_V1, tuple(evidence), subjects=tuple(subjects))
    output = {
        "qualification_run_id": result.qualification_run_id,
        "status": result.status.value,
        "reason_code": result.reason_code.value,
        "content_identity": str(result.content_identity),
        "repository_identity": str(repository_identity),
        "cases": [
            {
                "case_id": r.case_id,
                "status": r.status.value,
                "reason": r.reason_code.value,
                "content_identity": str(r.content_identity),
            }
            for r in result.case_results
        ],
        "evidence": [
            e.canonical_value() | {"content_identity": str(e.content_identity)} for e in evidence
        ],
        "subjects": [
            {
                "subject_id": s.subject_id,
                "source_module": s.source_module,
                "produced_by": s.produced_by,
                "content": json.loads(s.content),
                "content_identity": str(s.content_identity),
            }
            for s in subjects
        ],
    }
    destination = Path("eng-test-001-qualification.json") if output_path is None else output_path
    destination.write_bytes(canonical_json_bytes(output))
    return result, tuple(subjects), tuple(evidence), source_after


def main():
    result, _, _, _ = collect()
    print(result.status.value, result.qualification_run_id)
    return 0 if result.status.value == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
