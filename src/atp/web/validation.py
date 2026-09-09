"""Read-only integrity checks. No domain evaluation or mutable operation."""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import NoReturn, Union, get_args, get_origin, get_type_hints

from atp.accounting.inspection import validate_accounting_replay, validate_accounting_valuation
from atp.accounting.model import AccountingReplayResult, AccountingValuation
from atp.accounting.policy import ACCOUNTING_POLICY_V1
from atp.backtesting.engine import BacktestInput
from atp.backtesting.inspection import backtest_causal_time
from atp.backtesting.model import BacktestResult
from atp.backtesting.policy import SimulationPolicy
from atp.observability.audit import AuditJournal, validate_journal
from atp.observability.events import EVENT_SCHEMA_VERSION, SENSITIVE_KEYS, ObservabilityStatus
from atp.ops.inspection import validate_health_result, validate_readiness_result
from atp.ops.model import (
    HealthStatus,
    OperationalHealthEvidence,
    OperationalPolicy,
    OperationalReadinessResult,
)
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import require_utc
from atp.test_qualification.inspection import validate_qualification_result
from atp.test_qualification.model import (
    QualificationSuiteResult,
)
from atp.web.model import ArtifactReference, HealthResult, WebError, WebReasonCode


class InvalidArtifact(ValueError):
    def __init__(self, reason: WebReasonCode) -> None:
        self.reason = reason


def fail(reason: WebReasonCode = WebReasonCode.ARTIFACT_INTEGRITY_FAILURE) -> NoReturn:
    raise InvalidArtifact(reason)


_KNOWN_TYPES = frozenset(
    value
    for name, module in tuple(sys.modules.items())
    if name.startswith("atp.") and module is not None
    for value in tuple(vars(module).values())
    if isinstance(value, type) and (is_dataclass(value) or issubclass(value, Enum))
)


def _registered(cls: type[object]) -> bool:
    return cls in _KNOWN_TYPES


def _matches(value: object, annotation: object) -> bool:
    if annotation is object:
        return True
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        return any(_matches(value, a) for a in args)
    if origin is frozenset:
        return type(value) is frozenset and all(_matches(v, args[0]) for v in value)
    if origin is tuple:
        if type(value) is not tuple:
            return False
        assert isinstance(value, tuple)
        return (
            all(_matches(v, args[0]) for v in value)
            if len(args) == 2 and args[1] is Ellipsis
            else len(value) == len(args)
            and all(_matches(v, a) for v, a in zip(value, args, strict=False))
        )
    if origin in (dict, Mapping):
        return type(value) in (dict, types.MappingProxyType) and all(
            _matches(k, args[0]) and _matches(v, args[1])
            for k, v in value.items()  # type: ignore[attr-defined]
        )
    return type(value) is annotation


def safe_value(value: object, depth: int = 0) -> object:
    """Reject unknown objects before calling any representation or domain method."""
    if depth > 60:
        fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    cls = type(value)
    if value is None or cls in (bool, int):
        return value
    if cls is str:
        assert isinstance(value, str)
        if any(
            marker in value.lower()
            for marker in (
                "bearer ",
                "-----begin private key",
                "api_key=",
                "password=",
                "access_token=",
            )
        ):
            fail(WebReasonCode.SENSITIVE_DATA_DETECTED)
        return value
    if cls is Decimal:
        assert isinstance(value, Decimal)
        if not value.is_finite():
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
        return str(value)
    if cls is datetime:
        assert isinstance(value, datetime)
        require_utc(value)
        return value.isoformat()
    if cls is bytes:
        assert isinstance(value, bytes)
        # Canonical domain payload bytes remain private; inspect structured keys.
        safe_value(json.loads(value), depth + 1)
        return value.hex()
    if cls is frozenset:
        assert isinstance(value, frozenset)
        return sorted(
            [safe_value(v, depth + 1) for v in value], key=lambda v: json.dumps(v, sort_keys=True)
        )
    if cls in (tuple, list):
        return [safe_value(v, depth + 1) for v in value]  # type: ignore[attr-defined]
    if cls in (dict, types.MappingProxyType):
        result = {}
        for key, item in value.items():  # type: ignore[attr-defined]
            if type(key) is not str:
                fail(WebReasonCode.INVALID_WEB_ARTIFACT)
            if key.lower() in SENSITIVE_KEYS:
                fail(WebReasonCode.SENSITIVE_DATA_DETECTED)
            result[key] = safe_value(item, depth + 1)
        return result
    if not _registered(cls):
        fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    if isinstance(value, Enum):
        return value.value
    if not is_dataclass(value) or isinstance(value, type):
        fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    hints = get_type_hints(cls)
    result = {}
    for field in fields(value):
        item = getattr(value, field.name)
        if not _matches(item, hints[field.name]):
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
        result[field.name] = safe_value(item, depth + 1)
    return {"type": cls.__module__ + "." + cls.__qualname__, "fields": result}


def fingerprint(value: object) -> ContentIdentity:
    return ContentIdentity.from_canonical(safe_value(value))


def _post_init(value: object) -> None:
    """All fields were structurally checked before invoking accepted model guards."""
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _post_init(getattr(value, field.name))
        method = getattr(type(value), "__post_init__", None)
        if method is not None:
            method(value)
    elif type(value) in (tuple, list):
        for item in value:  # type: ignore[attr-defined]
            _post_init(item)


def validate_domain(value: object, causal: object = None) -> ContentIdentity:
    safe_value(value)
    safe_value(causal)
    _post_init(value)
    _post_init(causal)
    if type(value) is HealthResult:
        assert isinstance(value, HealthResult)
        if (
            type(value.health_status) is not HealthStatus
            or type(value.evidence) is not OperationalHealthEvidence
        ):
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
        if not validate_health_result(value.health_status, value.evidence):
            fail()
        return fingerprint(value)
    if type(value) is OperationalReadinessResult:
        assert isinstance(value, OperationalReadinessResult)
        if value.ops_policy_identity != OperationalPolicy().content_identity:
            fail(WebReasonCode.UNSUPPORTED_ARTIFACT_VERSION)
        if not validate_readiness_result(value):
            fail()
    elif type(value) is QualificationSuiteResult:
        assert isinstance(value, QualificationSuiteResult)
        if value.suite_id != "ATP_V1_QUALIFICATION" or value.suite_version != "1.0":
            fail(WebReasonCode.UNSUPPORTED_ARTIFACT_VERSION)
        if not validate_qualification_result(value):
            fail()
    elif type(value) is AuditJournal:
        assert isinstance(value, AuditJournal)
        if value.schema_version != EVENT_SCHEMA_VERSION:
            fail(WebReasonCode.UNSUPPORTED_ARTIFACT_VERSION)
        if validate_journal(value).status is not ObservabilityStatus.ACCEPTED:
            fail()
    elif type(value) is BacktestResult:
        assert isinstance(value, BacktestResult)
        if value.simulation_policy_identity != SimulationPolicy.v1().content_identity:
            fail(WebReasonCode.UNSUPPORTED_ARTIFACT_VERSION)
        if type(causal) is not BacktestInput:
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
        assert isinstance(causal, BacktestInput)
        if value.input_identity != causal.content_identity or len(value.steps) > len(causal.steps):
            fail(WebReasonCode.WEB_STATE_INCONSISTENT)
        if backtest_causal_time(value, causal) is None:
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    elif type(value) is AccountingReplayResult:
        if not validate_accounting_replay(value):
            fail()
    elif type(value) is AccountingValuation:
        assert isinstance(value, AccountingValuation)
        if value.accounting_policy_identity != ACCOUNTING_POLICY_V1.content_identity:
            fail(WebReasonCode.UNSUPPORTED_ARTIFACT_VERSION)
        if not validate_accounting_valuation(value):
            fail()
    else:
        fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    identity = value.content_identity
    if type(identity) is not ContentIdentity:
        fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    assert isinstance(identity, ContentIdentity)
    return identity


# Expected external-data failures only; programming errors in HTTP handlers propagate.
INPUT_ERRORS = (AttributeError, TypeError, ValueError, ArithmeticError, KeyError)


def reference(artifact: object, *, causal_input: object = None) -> ArtifactReference | WebError:
    try:
        identity = validate_domain(artifact, causal_input)
        return ArtifactReference(
            artifact, identity, fingerprint((artifact, causal_input)), causal_input
        )
    except InvalidArtifact as error:
        return WebError(reason_code=error.reason, resource="artifact")
    except ValidationError:
        return WebError(reason_code=WebReasonCode.ARTIFACT_INTEGRITY_FAILURE, resource="artifact")
    except INPUT_ERRORS:
        return WebError(reason_code=WebReasonCode.INVALID_WEB_ARTIFACT, resource="artifact")


def verify(ref: ArtifactReference) -> object:
    if fingerprint((ref.artifact, ref.causal_input)) != ref.fingerprint:
        fail()
    if validate_domain(ref.artifact, ref.causal_input) != ref.content_identity:
        fail()
    return ref.artifact
