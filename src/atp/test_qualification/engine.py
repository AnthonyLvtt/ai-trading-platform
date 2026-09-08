"""Pure, fail-closed qualification of explicit invariant observations.

Producers exercise invariants; this engine validates and evaluates their evidence.
Hash integrity is not producer authentication or release authorization.
"""

from __future__ import annotations

import json
from dataclasses import replace

from atp.observability.events import SENSITIVE_KEYS
from atp.shared.identity import ContentIdentity
from atp.shared.serialization import canonical_json_bytes
from atp.test_qualification.catalogue import cases_v1, suite_v1
from atp.test_qualification.model import (
    EvidenceBuildResult,
    EvidenceType,
    QualificationCase,
    QualificationCaseResult,
    QualificationEvidence,
    QualificationPolicy,
    QualificationSubject,
    QualificationSuite,
    QualificationSuiteResult,
)
from atp.test_qualification.model import (
    QualificationReasonCode as Reason,
)
from atp.test_qualification.model import (
    QualificationStatus as Status,
)


class _Invalid(Exception):
    def __init__(self, reason: Reason) -> None:
        self.reason = reason


def _require(condition: bool, reason: Reason = Reason.EVIDENCE_INVALID) -> None:
    if not condition:
        raise _Invalid(reason)


def _safe_json(value: object) -> None:
    if type(value) is dict:
        for key, item in value.items():
            _require(type(key) is str)
            _require(key.casefold() not in SENSITIVE_KEYS, Reason.SECRET_MATERIAL_DETECTED)
            _safe_json(item)
    elif type(value) is list:
        for item in value:
            _safe_json(item)
    else:
        _require(value is None or type(value) in (str, int, bool))
        if type(value) is str:
            # Block recognizable credential material even under an innocuous key.
            _require(
                not any(
                    marker in value.casefold()
                    for marker in (
                        "-----begin",
                        "bearer ",
                        "ghp_",
                        "github_pat_",
                        "sk-proj-",
                    )
                ),
                Reason.SECRET_MATERIAL_DETECTED,
            )


def _decode(value: object) -> dict[str, object]:
    _require(type(value) is bytes)
    assert isinstance(value, bytes)
    decoded = json.loads(value)
    _safe_json(decoded)
    _require(type(decoded) is dict)
    _require(canonical_json_bytes(decoded) == value, Reason.EVIDENCE_NON_DETERMINISTIC)
    return decoded  # type: ignore[no-any-return]


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value.strip() == value


def _identity(value: object) -> bool:
    if type(value) is not ContentIdentity:
        return False
    assert isinstance(value, ContentIdentity)
    value.__post_init__()
    return True


def _policy(value: object) -> ContentIdentity:
    _require(type(value) is QualificationPolicy, Reason.POLICY_MISMATCH)
    assert isinstance(value, QualificationPolicy)
    try:
        value.__post_init__()
    except Exception as error:
        raise _Invalid(Reason.POLICY_MISMATCH) from error
    return value.content_identity


def _case(value: object) -> QualificationCase:
    _require(type(value) is QualificationCase, Reason.CASE_DEFINITION_INVALID)
    assert isinstance(value, QualificationCase)
    _require(any(value == case for case in cases_v1()), Reason.CASE_DEFINITION_INVALID)
    return value


def _subject(value: object) -> QualificationSubject:
    _require(type(value) is QualificationSubject)
    assert isinstance(value, QualificationSubject)
    _require(all(_text(x) for x in (value.subject_id, value.source_module, value.produced_by)))
    _decode(value.content)
    return value


def build_evidence(
    *,
    evidence_id: object,
    case: object,
    check_id: object,
    subject: object,
    payload: object,
) -> EvidenceBuildResult:
    """Build one typed check observation; reject contamination without copying it."""
    try:
        definition = _case(case)
        source = _subject(subject)
        _safe_json(payload)
        _require(type(payload) is dict)
        _require(_text(evidence_id) and _text(check_id))
        assert isinstance(evidence_id, str) and isinstance(check_id, str)
        evidence = QualificationEvidence(
            evidence_id,
            definition.required_evidence_types[0],
            source.source_module,
            source.produced_by,
            definition.case_id,
            check_id,
            source.subject_id,
            source.content_identity,
            definition.policy_id,
            definition.policy_version,
            canonical_json_bytes(payload),
            ContentIdentity.from_text("pending"),
        )
        evidence = replace(
            evidence, content_identity=ContentIdentity.from_canonical(evidence.canonical_value())
        )
        _evidence(evidence, definition, (source,))
        return EvidenceBuildResult(None, None, evidence)
    except _Invalid as error:
        return EvidenceBuildResult(Status.BLOCKED, error.reason)
    except Exception:  # noqa: BLE001 - public evidence trust boundary
        return EvidenceBuildResult(Status.BLOCKED, Reason.EVIDENCE_INVALID)


def _evidence(
    value: object,
    case: QualificationCase,
    subjects: tuple[QualificationSubject, ...],
) -> tuple[QualificationEvidence, dict[str, object]]:
    _require(type(value) is QualificationEvidence)
    assert isinstance(value, QualificationEvidence)
    _require(type(value.evidence_type) is EvidenceType)
    _require(
        all(
            _text(x)
            for x in (
                value.evidence_id,
                value.source_module,
                value.produced_by,
                value.case_id,
                value.check_id,
                value.subject_id,
                value.policy_id,
                value.policy_version,
            )
        )
    )
    payload = _decode(value.payload)
    _require(_identity(value.content_identity) and _identity(value.subject_content_identity))
    _safe_json(value.canonical_value())
    _require(
        ContentIdentity.from_canonical(value.canonical_value()) == value.content_identity,
        Reason.EVIDENCE_IDENTITY_MISMATCH,
    )
    _require(
        value.policy_id == case.policy_id and value.policy_version == case.policy_version,
        Reason.POLICY_MISMATCH,
    )
    _require(
        value.case_id == case.case_id
        and value.check_id in case.required_checks
        and value.source_module == case.source_module
        and value.evidence_type in case.required_evidence_types,
        Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE,
    )
    matches = [s for s in subjects if s.subject_id == value.subject_id]
    _require(len(matches) == 1, Reason.EVIDENCE_MISSING)
    source = _subject(matches[0])
    _require(
        source.content_identity == value.subject_content_identity, Reason.EVIDENCE_IDENTITY_MISMATCH
    )
    _require(
        source.source_module == value.source_module and source.produced_by == value.produced_by,
        Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE,
    )
    # No generic scalar coercions: e.g. 1 is not an acceptable boolean observation.
    _require(set(payload) == {"satisfied", "applicable", "reproducible", "observation_id"})
    _require(all(type(payload[k]) is bool for k in ("satisfied", "applicable", "reproducible")))
    _require(_text(payload["observation_id"]))
    manifest = _decode(source.content)
    observations = manifest.get("observations")
    _require(type(observations) is dict)
    assert isinstance(observations, dict)
    _require(
        observations.get(payload["observation_id"])
        == {
            "case_id": value.case_id,
            "check_id": value.check_id,
            "satisfied": payload["satisfied"],
            "applicable": payload["applicable"],
            "reproducible": payload["reproducible"],
        },
        Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE,
    )
    return value, payload


def _failure(case: QualificationCase, checks: list[str]) -> Reason:
    temporal = {
        "no_future",
        "available_at",
        "no_rewrite",
        "strict_order",
        "next_open",
        "no_current_close",
        "no_skip",
        "no_future_state",
        "causal_fills",
        "causal_time",
    }
    if any(check in temporal for check in checks):
        return Reason.TEMPORAL_CAUSALITY_VIOLATION
    if case.case_id == "Q-BOUNDARY-001":
        return Reason.MODULE_BOUNDARY_VIOLATION
    if case.case_id == "Q-SECURITY-001":
        return Reason.SECURITY_INVARIANT_VIOLATION
    return Reason.INVARIANT_VIOLATED


def evaluate_case(
    case: object,
    evidence: object,
    *,
    subjects: object = (),
    policy: object = QualificationPolicy(),
) -> QualificationCaseResult:
    """Evaluate a complete canonical evidence set; missing proof always blocks."""
    definition: QualificationCase | None = None
    used: list[QualificationEvidence] = []
    policy_identity = QualificationPolicy().content_identity
    status, reason = Status.BLOCKED, Reason.EVIDENCE_INVALID
    try:
        policy_identity = _policy(policy)
        definition = _case(case)
        _require(type(evidence) is tuple and type(subjects) is tuple)
        assert isinstance(evidence, tuple) and isinstance(subjects, tuple)
        sources = tuple(_subject(s) for s in subjects)
        _require(
            len({s.subject_id for s in sources}) == len(sources),
            Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE,
        )
        validated = [_evidence(e, definition, sources) for e in evidence]
        used = sorted((e for e, _ in validated), key=lambda e: e.evidence_id)
        _require(len({e.evidence_id for e in used}) == len(used), Reason.DUPLICATE_EVIDENCE)
        _require(len({e.check_id for e in used}) == len(used), Reason.DUPLICATE_EVIDENCE)
        _require(
            {e.check_id for e in used} == set(definition.required_checks), Reason.EVIDENCE_MISSING
        )
        _require(all(p["reproducible"] for _, p in validated), Reason.EVIDENCE_NON_DETERMINISTIC)
        _require(all(p["applicable"] for _, p in validated), Reason.CASE_NOT_APPLICABLE)
        violated = [e.check_id for e, p in validated if not p["satisfied"]]
        status = Status.FAILED if violated else Status.PASSED
        reason = _failure(definition, violated) if violated else Reason.QUALIFICATION_PASSED
    except _Invalid as error:
        reason = error.reason
    except Exception:  # noqa: BLE001 - public qualification trust boundary
        reason = Reason.EVIDENCE_INVALID
    # On unsafe evidence no user-controlled identifiers or payloads are echoed.
    if status is Status.BLOCKED and reason not in (
        Reason.EVIDENCE_MISSING,
        Reason.EVIDENCE_NON_DETERMINISTIC,
        Reason.CASE_NOT_APPLICABLE,
    ):
        used = []
    return QualificationCaseResult(
        definition.case_id if definition else "invalid-case",
        definition.content_identity
        if definition
        else ContentIdentity.from_text(type(case).__name__),
        status,
        reason,
        tuple(e.evidence_id for e in used),
        tuple(e.content_identity for e in used),
        policy_identity,
    )


def evaluate_suite(
    suite: object,
    cases: object,
    evidence: object,
    *,
    subjects: object = (),
    policy: object = QualificationPolicy(),
) -> QualificationSuiteResult:
    canonical_suite = suite_v1()
    results: tuple[QualificationCaseResult, ...] = ()
    status, reason = Status.BLOCKED, Reason.EVIDENCE_INVALID
    try:
        _policy(policy)
        _require(
            type(suite) is QualificationSuite and suite == canonical_suite, Reason.SUITE_INCOMPLETE
        )
        _require(type(cases) is tuple and type(evidence) is tuple and type(subjects) is tuple)
        assert isinstance(cases, tuple) and isinstance(evidence, tuple)
        definitions = tuple(_case(c) for c in cases)
        _require(len({c.case_id for c in definitions}) == len(definitions), Reason.DUPLICATE_CASE)
        _require(
            {c.case_id for c in definitions} == set(canonical_suite.required_case_ids),
            Reason.SUITE_INCOMPLETE,
        )
        _require(all(type(e) is QualificationEvidence for e in evidence))
        _require(all(_text(e.evidence_id) for e in evidence))
        _require(len({e.evidence_id for e in evidence}) == len(evidence), Reason.DUPLICATE_EVIDENCE)
        _require(
            all(e.case_id in canonical_suite.required_case_ids for e in evidence),
            Reason.EVIDENCE_PROVENANCE_INCOMPATIBLE,
        )
        results = tuple(
            evaluate_case(
                c,
                tuple(e for e in evidence if e.case_id == c.case_id),
                subjects=subjects,
                policy=policy,
            )
            for c in sorted(definitions, key=lambda c: c.case_id)
        )
        status = next(
            s
            for s in (Status.BLOCKED, Status.FAILED, Status.PASSED)
            if any(r.status is s for r in results)
        )
        reason = next(r.reason_code for r in results if r.status is status)
    except _Invalid as error:
        reason = error.reason
    except Exception:  # noqa: BLE001 - public suite trust boundary
        reason = Reason.EVIDENCE_INVALID
    return QualificationSuiteResult(
        canonical_suite.suite_id,
        canonical_suite.suite_version,
        canonical_suite.content_identity,
        status,
        reason,
        results,
        QualificationPolicy().content_identity,
    )
