"""Public verification of qualification result integrity, without collecting/evaluating evidence."""

from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.test_qualification.catalogue import cases_v1, suite_v1
from atp.test_qualification.model import (
    QualificationCaseResult,
    QualificationPolicy,
    QualificationSuiteResult,
)
from atp.test_qualification.model import (
    QualificationReasonCode as Reason,
)
from atp.test_qualification.model import (
    QualificationStatus as Status,
)


def _identity(value: object) -> bool:
    if type(value) is not ContentIdentity:
        return False
    assert isinstance(value, ContentIdentity)
    if type(value.algorithm) is not str or type(value.digest) is not str:
        return False
    value.__post_init__()
    return True


def validate_qualification_result(value: object) -> bool:
    """Stored suite hash and canonical case links must match; PASSED is not authorization."""
    if type(value) is not QualificationSuiteResult:
        return False
    assert isinstance(value, QualificationSuiteResult)
    try:
        suite, policy = suite_v1(), QualificationPolicy()
        if (
            type(value.suite_id) is not str
            or value.suite_id != suite.suite_id
            or type(value.suite_version) is not str
            or value.suite_version != suite.suite_version
            or type(value.status) is not Status
            or type(value.reason_code) is not Reason
            or type(value.case_results) is not tuple
        ):
            return False
        if not all(
            _identity(i)
            for i in (
                value.content_identity,
                value.suite_identity,
                value.qualification_policy_identity,
            )
        ):
            return False
        if (
            value.suite_identity != suite.content_identity
            or value.qualification_policy_identity != policy.content_identity
        ):
            return False
        definitions = {c.case_id: c for c in cases_v1()}
        names = []
        evidence_names: list[str] = []
        for case in value.case_results:
            if (
                type(case) is not QualificationCaseResult
                or type(case.case_id) is not str
                or type(case.status) is not Status
                or type(case.reason_code) is not Reason
                or type(case.evaluated_evidence_ids) is not tuple
                or type(case.evaluated_evidence_identities) is not tuple
            ):
                return False
            definition = definitions.get(case.case_id)
            if (
                definition is None
                or not _identity(case.case_identity)
                or case.case_identity != definition.content_identity
            ):
                return False
            if (
                not _identity(case.qualification_policy_identity)
                or case.qualification_policy_identity != policy.content_identity
            ):
                return False
            if not all(
                type(n) is str and n and n.strip() == n for n in case.evaluated_evidence_ids
            ):
                return False
            if not all(_identity(i) for i in case.evaluated_evidence_identities):
                return False
            if (
                len(case.evaluated_evidence_ids) != len(case.evaluated_evidence_identities)
                or tuple(sorted(set(case.evaluated_evidence_ids))) != case.evaluated_evidence_ids
            ):
                return False
            if case.status is Status.PASSED:
                if case.reason_code is not Reason.QUALIFICATION_PASSED or len(
                    case.evaluated_evidence_ids
                ) != len(definition.required_checks):
                    return False
            elif case.reason_code is Reason.QUALIFICATION_PASSED:
                return False
            names.append(case.case_id)
            evidence_names.extend(case.evaluated_evidence_ids)
        if len(set(names)) != len(names) or len(set(evidence_names)) != len(evidence_names):
            return False
        if value.case_results:
            if tuple(names) != suite.required_case_ids:
                return False
            status = next(
                s
                for s in (Status.BLOCKED, Status.FAILED, Status.PASSED)
                if any(c.status is s for c in value.case_results)
            )
            reason = next(c.reason_code for c in value.case_results if c.status is status)
            if value.status is not status or value.reason_code is not reason:
                return False
        elif value.status is not Status.BLOCKED or value.reason_code is Reason.QUALIFICATION_PASSED:
            return False
        expected = value.recompute_content_identity()
        return (
            value.content_identity == expected
            and value.qualification_run_id == f"qualification-run:{expected}"
        )
    except (AttributeError, TypeError, ValueError, KeyError, ValidationError):
        return False
