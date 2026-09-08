from dataclasses import replace

import pytest

from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.test_qualification import (
    CASES_V1,
    SUITE_V1,
    QualificationPolicy,
    QualificationSubject,
    build_evidence,
    evaluate_case,
    evaluate_suite,
)
from atp.test_qualification import (
    QualificationReasonCode as Reason,
)
from atp.test_qualification import (
    QualificationStatus as Status,
)


def bundle(cases=CASES_V1, *, failed=(), unavailable=()):
    subjects, evidence = [], []
    for case in cases:
        observations = {
            check: {
                "case_id": case.case_id,
                "check_id": check,
                "satisfied": (case.case_id, check) not in failed,
                "reproducible": (case.case_id, check) not in unavailable,
                "applicable": True,
            }
            for check in case.required_checks
        }
        subject = QualificationSubject(
            case.case_id,
            case.source_module,
            "unit-fixture-v1",
            canonical_json_bytes({"observations": observations}),
        )
        subjects.append(subject)
        for check in case.required_checks:
            observation = observations[check]
            result = build_evidence(
                evidence_id=f"{case.case_id}:{check}",
                case=case,
                check_id=check,
                subject=subject,
                payload={k: observation[k] for k in ("satisfied", "reproducible", "applicable")}
                | {"observation_id": check},
            )
            assert result.evidence is not None
            evidence.append(result.evidence)
    return tuple(subjects), tuple(evidence)


def test_identical_and_reordered_evidence_produce_identical_suite():
    subjects, evidence = bundle()
    first = evaluate_suite(SUITE_V1, CASES_V1, evidence, subjects=subjects)
    second = evaluate_suite(SUITE_V1, CASES_V1[::-1], evidence[::-1], subjects=subjects[::-1])
    assert first == second
    assert first.content_identity == second.content_identity
    assert first.qualification_run_id == second.qualification_run_id
    assert first.status is Status.PASSED
    assert len(first.case_results) == 10


def test_changed_evidence_changes_identity_and_fails_invariant():
    subjects, evidence = bundle()
    good = evaluate_suite(SUITE_V1, CASES_V1, evidence, subjects=subjects)
    subjects, evidence = bundle(failed=(("Q-BACKTEST-001", "no_current_close"),))
    bad = evaluate_suite(SUITE_V1, CASES_V1, evidence, subjects=subjects)
    assert bad.status is Status.FAILED
    assert bad.reason_code is Reason.TEMPORAL_CAUSALITY_VIOLATION
    assert bad.content_identity != good.content_identity


@pytest.mark.parametrize(
    "case_id,check,reason",
    [
        ("Q-BOUNDARY-001", "risk", Reason.MODULE_BOUNDARY_VIOLATION),
        ("Q-SECURITY-001", "no_live", Reason.SECURITY_INVARIANT_VIOLATION),
        ("Q-ACCOUNTING-001", "equity", Reason.INVARIANT_VIOLATED),
    ],
)
def test_violations_have_explicit_reason(case_id, check, reason):
    subjects, evidence = bundle(failed=((case_id, check),))
    result = evaluate_suite(SUITE_V1, CASES_V1, evidence, subjects=subjects)
    assert result.status is Status.FAILED and result.reason_code is reason


def test_missing_evidence_blocks_and_takes_precedence_over_failure():
    subjects, evidence = bundle(failed=(("Q-BACKTEST-001", "no_current_close"),))
    result = evaluate_suite(SUITE_V1, CASES_V1, evidence[1:], subjects=subjects)
    assert result.status is Status.BLOCKED
    assert result.reason_code is Reason.EVIDENCE_MISSING
    assert any(r.status is Status.FAILED for r in result.case_results)


def test_incomplete_and_duplicate_suite_cases_block():
    subjects, evidence = bundle()
    assert (
        evaluate_suite(SUITE_V1, CASES_V1[1:], evidence, subjects=subjects).reason_code
        is Reason.SUITE_INCOMPLETE
    )
    assert (
        evaluate_suite(SUITE_V1, CASES_V1 + (CASES_V1[0],), evidence, subjects=subjects).reason_code
        is Reason.DUPLICATE_CASE
    )


def test_duplicate_evidence_is_not_deduplicated():
    subjects, evidence = bundle((CASES_V1[0],))
    result = evaluate_case(CASES_V1[0], evidence + (evidence[0],), subjects=subjects)
    assert result.status is Status.BLOCKED
    assert result.reason_code is Reason.DUPLICATE_EVIDENCE


def test_well_typed_tampered_evidence_is_detected():
    subjects, evidence = bundle((CASES_V1[0],))
    object.__setattr__(
        evidence[0],
        "payload",
        canonical_json_bytes(
            {
                "satisfied": False,
                "applicable": True,
                "reproducible": True,
                "observation_id": CASES_V1[0].required_checks[0],
            }
        ),
    )
    result = evaluate_case(CASES_V1[0], evidence, subjects=subjects)
    assert result.status is Status.BLOCKED
    assert result.reason_code is Reason.EVIDENCE_IDENTITY_MISMATCH


def test_rehashed_forged_observation_still_requires_source_compatibility():
    subjects, evidence = bundle((CASES_V1[0],))
    altered = replace(
        evidence[0],
        payload=canonical_json_bytes(
            {
                "satisfied": False,
                "applicable": True,
                "reproducible": True,
                "observation_id": CASES_V1[0].required_checks[0],
            }
        ),
    )
    altered = replace(
        altered, content_identity=ContentIdentity.from_canonical(altered.canonical_value())
    )
    result = evaluate_case(CASES_V1[0], (altered,) + evidence[1:], subjects=subjects)
    assert result.reason_code is Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE


def test_changed_subject_is_detected():
    subjects, evidence = bundle((CASES_V1[0],))
    altered = replace(subjects[0], content=canonical_json_bytes({"observations": {}}))
    result = evaluate_case(CASES_V1[0], evidence, subjects=(altered,))
    assert result.reason_code is Reason.EVIDENCE_IDENTITY_MISMATCH


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_id", "other"),
        ("version", "1.1"),
        ("deterministic", False),
        ("network_required", True),
        ("secret_material_allowed", True),
    ],
)
def test_non_v1_policy_construction_and_mutation_fail_closed(field, value):
    with pytest.raises(ValueError):
        replace(QualificationPolicy(), **{field: value})
    policy = QualificationPolicy()
    object.__setattr__(policy, field, value)
    result = evaluate_suite(SUITE_V1, CASES_V1, (), policy=policy)
    assert result.status is Status.BLOCKED and result.reason_code is Reason.POLICY_MISMATCH


class Explosive:
    def __str__(self):
        raise AssertionError("external str must not be called")

    def __repr__(self):
        raise AssertionError("external repr must not be called")


@pytest.mark.parametrize(
    "value", [None, 1, "green CI", [], Explosive()], ids=["none", "int", "str", "list", "object"]
)
def test_runtime_boundary_blocks_malformed_inputs(value):
    assert evaluate_case(value, value, subjects=value).status is Status.BLOCKED
    assert evaluate_suite(value, value, value, subjects=value).status is Status.BLOCKED
    assert (
        build_evidence(
            evidence_id=value, case=value, check_id=value, subject=value, payload=value
        ).status
        is Status.BLOCKED
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("payload", object()),
        ("content_identity", "fake"),
        ("evidence_type", "UNIT_TEST_RESULT"),
        ("subject_content_identity", []),
        ("check_id", 1),
    ],
)
def test_nested_runtime_corruption_blocks(field, value):
    subjects, evidence = bundle((CASES_V1[0],))
    object.__setattr__(evidence[0], field, value)
    result = evaluate_case(CASES_V1[0], evidence, subjects=subjects)
    assert result.status is Status.BLOCKED


def test_secret_payload_blocks_without_echoing_secret():
    subjects, evidence = bundle((CASES_V1[0],))
    marker = "fixture-sensitive-value"
    object.__setattr__(
        evidence[0], "payload", canonical_json_bytes({"nested": [{"api_key": marker}]})
    )
    result = evaluate_case(CASES_V1[0], evidence, subjects=subjects)
    assert result.reason_code is Reason.SECRET_MATERIAL_DETECTED
    assert marker not in repr(result)
    assert result.evaluated_evidence_ids == ()


def test_non_reproducible_evidence_blocks():
    subjects, evidence = bundle(unavailable=(("Q-SHARED-001", "content_identity"),))
    result = evaluate_suite(SUITE_V1, CASES_V1, evidence, subjects=subjects)
    assert result.status is Status.BLOCKED
    assert result.reason_code is Reason.EVIDENCE_NON_DETERMINISTIC


def test_correct_keys_with_non_boolean_values_block():
    subjects, _ = bundle((CASES_V1[0],))
    result = build_evidence(
        evidence_id="bad",
        case=CASES_V1[0],
        check_id=CASES_V1[0].required_checks[0],
        subject=subjects[0],
        payload={
            "satisfied": 1,
            "applicable": True,
            "reproducible": True,
            "observation_id": CASES_V1[0].required_checks[0],
        },
    )
    assert result.status is Status.BLOCKED


def test_no_subject_or_wrong_producer_cannot_support_qualification():
    subjects, evidence = bundle((CASES_V1[0],))
    assert evaluate_case(CASES_V1[0], evidence).reason_code is Reason.EVIDENCE_MISSING
    subject = replace(subjects[0], produced_by="another-producer")
    result = evaluate_case(CASES_V1[0], evidence, subjects=(subject,))
    assert result.reason_code is Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE


def test_noncanonical_source_and_mutated_definitions_are_blocked():
    subjects, evidence = bundle((CASES_V1[0],))
    altered = replace(subjects[0], content=b'{ "observations": {} }')
    assert (
        evaluate_case(CASES_V1[0], evidence, subjects=(altered,)).reason_code
        is Reason.EVIDENCE_NON_DETERMINISTIC
    )
    altered_case = replace(CASES_V1[0], required_checks=())
    assert evaluate_case(altered_case, ()).reason_code is Reason.CASE_DEFINITION_INVALID
    assert (
        evaluate_suite(replace(SUITE_V1, suite_version="2"), CASES_V1, ()).status is Status.BLOCKED
    )


def test_not_applicable_is_blocked_not_skipped():
    case = CASES_V1[0]
    subjects, evidence = bundle((case,))
    import json

    content = json.loads(subjects[0].content)
    content["observations"][case.required_checks[0]]["applicable"] = False
    source = replace(subjects[0], content=canonical_json_bytes(content))
    rebuilt = []
    for check in case.required_checks:
        observation = content["observations"][check]
        result = build_evidence(
            evidence_id=f"{case.case_id}:{check}",
            case=case,
            check_id=check,
            subject=source,
            payload={k: observation[k] for k in ("satisfied", "applicable", "reproducible")}
            | {"observation_id": check},
        )
        assert result.evidence is not None
        rebuilt.append(result.evidence)
    qualified = evaluate_case(case, tuple(rebuilt), subjects=(source,))
    assert qualified.status is Status.BLOCKED
    assert qualified.reason_code is Reason.CASE_NOT_APPLICABLE


def test_suite_rejects_duplicate_evidence_before_aggregation():
    subjects, evidence = bundle()
    result = evaluate_suite(SUITE_V1, CASES_V1, evidence + (evidence[0],), subjects=subjects)
    assert result.status is Status.BLOCKED
    assert result.reason_code is Reason.DUPLICATE_EVIDENCE


def test_corrupt_identity_does_not_invoke_external_conversion():
    class Canary:
        called = False

        def __str__(self):
            Canary.called = True
            raise AssertionError("must not stringify external identity")

    subjects, evidence = bundle((CASES_V1[0],))
    object.__setattr__(evidence[0], "subject_content_identity", Canary())
    assert evaluate_case(CASES_V1[0], evidence, subjects=subjects).status is Status.BLOCKED
    assert not Canary.called


def test_mutation_of_exported_case_does_not_change_normative_catalogue():
    case = CASES_V1[0]
    original = case.required_checks
    try:
        object.__setattr__(case, "required_checks", ())
        assert evaluate_case(case, ()).reason_code is Reason.CASE_DEFINITION_INVALID
    finally:
        object.__setattr__(case, "required_checks", original)


def test_mutation_of_exported_suite_cannot_qualify_empty_suite():
    original = SUITE_V1.required_case_ids
    try:
        object.__setattr__(SUITE_V1, "required_case_ids", ())
        result = evaluate_suite(SUITE_V1, (), ())
        assert result.status is Status.BLOCKED
    finally:
        object.__setattr__(SUITE_V1, "required_case_ids", original)
