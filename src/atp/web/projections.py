"""Safe summaries of injected facts; no engine is invoked."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast

from atp.accounting.model import AccountingReplayResult, AccountingValuation
from atp.backtesting.engine import BacktestInput
from atp.backtesting.model import BacktestResult, SimulatedOrderOutcome
from atp.observability.adapters import _backtest_occurred_at
from atp.observability.audit import AuditJournal
from atp.ops.model import OperationalHealthEvidence, OperationalReadinessResult
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.test_qualification.model import QualificationSuiteResult
from atp.web.model import (
    AccountingSummaryView,
    ArtifactReference,
    AuditSummaryView,
    BacktestSummaryView,
    HealthResult,
    HealthView,
    QualificationView,
    ReadinessView,
    View,
    WebError,
    WebReasonCode,
    WebState,
)
from atp.web.validation import INPUT_ERRORS, InvalidArtifact, fail, safe_value, verify

ROUTES = (
    "/health",
    "/readiness",
    "/qualification",
    "/observability",
    "/backtest/latest",
    "/accounting/latest",
)


def _id(value: ContentIdentity | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _view(cls: type[View], payload: dict[str, object]) -> View:
    payload = {"status": "OK", **payload}
    safe_value(payload)
    initial = cls.model_validate({**payload, "view_identity": "pending"})
    canonical = initial.model_dump(mode="json", exclude={"view_identity"})
    return initial.model_copy(
        update={"view_identity": str(ContentIdentity.from_canonical(canonical))}
    )


def _state(state: WebState) -> None:
    safe_value(state)
    expected = (
        (state.health_result, HealthResult),
        (state.readiness_result, OperationalReadinessResult),
        (state.qualification_result, QualificationSuiteResult),
        (state.audit_journal, AuditJournal),
    )
    for ref, cls in expected:
        if ref is not None and type(verify(ref)) is not cls:
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    for refs, collection_type in (
        (state.backtest_results, BacktestResult),
        (state.accounting_replay_results, AccountingReplayResult),
        (state.accounting_valuations, AccountingValuation),
    ):
        ids = []
        for ref in refs:
            if type(verify(ref)) is not collection_type:
                fail(WebReasonCode.INVALID_WEB_ARTIFACT)
            ids.append(ref.content_identity)
        if len(set(ids)) != len(ids):
            fail(WebReasonCode.WEB_STATE_INCONSISTENT)
    if state.readiness_result and state.qualification_result:
        ready = cast(OperationalReadinessResult, state.readiness_result.artifact)
        if (
            ready.qualification_result_identity is not None
            and ready.qualification_result_identity != state.qualification_result.content_identity
        ):
            fail(WebReasonCode.WEB_STATE_INCONSISTENT)
    if state.accounting_replay_results:
        replays = [
            cast(AccountingReplayResult, r.artifact) for r in state.accounting_replay_results
        ]
        for ref in state.accounting_valuations:
            valuation = cast(AccountingValuation, ref.artifact)
            matching = [
                r
                for r in replays
                if r.final_state.content_identity == valuation.accounting_state_identity
            ]
            if not matching:
                fail(WebReasonCode.WEB_STATE_INCONSISTENT)
            source = matching[0].final_state
            if (
                source.cash != valuation.cash
                or source.position != valuation.position
                or source.cumulative_realized_pnl != valuation.realized_pnl
                or (
                    source.last_effective_at is not None
                    and source.last_effective_at > valuation.valuation_time
                )
            ):
                fail(WebReasonCode.WEB_STATE_INCONSISTENT)
    if state.backtest_results and state.accounting_replay_results:
        fills = {
            s.fill.content_identity
            for ref in state.backtest_results
            for s in cast(BacktestResult, ref.artifact).steps
            if s.fill is not None
        }
        for ref in state.accounting_replay_results:
            if any(
                e.provenance.simulated_fill_identity not in fills
                for e in cast(AccountingReplayResult, ref.artifact).ledger
            ):
                fail(WebReasonCode.WEB_STATE_INCONSISTENT)


def _required(ref: ArtifactReference | None) -> object:
    if ref is None:
        fail(WebReasonCode.ARTIFACT_NOT_AVAILABLE)
    assert ref is not None
    return ref.artifact


def _time(ref: ArtifactReference) -> datetime:
    result = cast(BacktestResult, ref.artifact)
    time = _backtest_occurred_at(result, cast(BacktestInput, ref.causal_input))
    assert time is not None
    return time


def _project(state: WebState, resource: str) -> View:
    if resource == "/health":
        health = cast(HealthResult, _required(state.health_result))
        evidence = cast(OperationalHealthEvidence, health.evidence)
        return _view(
            HealthView,
            {
                "health_status": health.health_status,
                "evidence_identity": str(evidence.content_identity),
            },
        )
    if resource == "/readiness":
        ready = cast(OperationalReadinessResult, _required(state.readiness_result))
        return _view(
            ReadinessView,
            {
                "readiness_status": ready.readiness_status.value,
                "reason_code": ready.reason_code.value,
                "environment": None if ready.environment is None else ready.environment.value,
                "config_identity": str(ready.config_identity),
                "readiness_identity": str(ready.content_identity),
                "qualification_result_identity": _id(ready.qualification_result_identity),
                "observability_evidence_identity": _id(ready.observability_evidence_identity),
            },
        )
    if resource == "/qualification":
        qualification = cast(QualificationSuiteResult, _required(state.qualification_result))
        return _view(
            QualificationView,
            {
                "qualification_status": qualification.status.value,
                "reason_code": qualification.reason_code.value,
                "suite_id": qualification.suite_id,
                "suite_version": qualification.suite_version,
                "qualification_run_id": qualification.qualification_run_id,
                "qualification_identity": str(qualification.content_identity),
                "case_summary": [
                    {
                        "case_id": c.case_id,
                        "status": c.status.value,
                        "reason_code": c.reason_code.value,
                        "content_identity": str(c.content_identity),
                    }
                    for c in qualification.case_results
                ],
            },
        )
    if resource == "/observability":
        journal = cast(AuditJournal, _required(state.audit_journal))
        return _view(
            AuditSummaryView,
            {
                "event_count": len(journal.events),
                "valid_integrity": True,
                "journal_identity": str(journal.content_identity),
                "first_event_identity": str(journal.events[0].content_identity)
                if journal.events
                else None,
                "last_event_identity": str(journal.events[-1].content_identity)
                if journal.events
                else None,
            },
        )
    if resource == "/backtest/latest":
        if not state.backtest_results:
            fail(WebReasonCode.ARTIFACT_NOT_AVAILABLE)
        ref = max(state.backtest_results, key=lambda r: (_time(r), str(r.content_identity)))
        result = cast(BacktestResult, ref.artifact)
        return _view(
            BacktestSummaryView,
            {
                "backtest_status": result.status.value,
                "reason_code": result.reason_code,
                "replay_id": str(result.backtest_run_id),
                "input_identity": str(result.input_identity),
                "result_identity": str(result.content_identity),
                "orders_count": result.number_of_orders,
                "fills_count": result.number_of_fills,
                "latest_causal_time": _time(ref).isoformat(),
                "blocked_count": sum(
                    s.outcome is SimulatedOrderOutcome.BLOCKED for s in result.steps
                ),
                "unfilled_count": sum(
                    s.outcome is SimulatedOrderOutcome.UNFILLED_END_OF_REPLAY for s in result.steps
                ),
            },
        )
    if state.accounting_valuations:
        valuation = max(
            (cast(AccountingValuation, r.artifact) for r in state.accounting_valuations),
            key=lambda v: (v.valuation_time, str(v.content_identity)),
        )
        return _view(
            AccountingSummaryView,
            {
                "source": "VALUATION",
                "accounting_status": valuation.status.value,
                "reason_code": valuation.reason_code,
                "currency": "USDT",
                "cash": _decimal(valuation.cash),
                "cumulative_realized_pnl": _decimal(valuation.realized_pnl),
                "unrealized_pnl": _decimal(valuation.unrealized_pnl),
                "equity": _decimal(valuation.equity),
                "valuation_time": valuation.valuation_time.isoformat(),
                "accounting_identity": str(valuation.content_identity),
            },
        )
    if not state.accounting_replay_results:
        fail(WebReasonCode.ARTIFACT_NOT_AVAILABLE)
    candidates = [cast(AccountingReplayResult, r.artifact) for r in state.accounting_replay_results]
    if any(r.final_state.last_effective_at is None for r in candidates):
        fail(WebReasonCode.INVALID_WEB_ARTIFACT)
    replay = max(
        candidates,
        key=lambda r: (cast(datetime, r.final_state.last_effective_at), str(r.content_identity)),
    )
    return _view(
        AccountingSummaryView,
        {
            "source": "REPLAY",
            "accounting_status": replay.status.value,
            "reason_code": replay.reason_code,
            "currency": replay.final_state.currency,
            "cash": _decimal(replay.final_state.cash),
            "cumulative_realized_pnl": _decimal(replay.final_state.cumulative_realized_pnl),
            "position_state": replay.final_state.position.status.value,
            "replay_identity": str(replay.content_identity),
        },
    )


def project(state: object, resource: str) -> View | WebError:
    if resource not in ROUTES:
        return WebError(reason_code=WebReasonCode.ARTIFACT_NOT_AVAILABLE, resource="unknown")
    try:
        if type(state) is not WebState:
            fail(WebReasonCode.INVALID_WEB_ARTIFACT)
        assert isinstance(state, WebState)
        _state(state)
    except InvalidArtifact as error:
        return WebError(reason_code=error.reason, resource=resource)
    except ValidationError:
        return WebError(reason_code=WebReasonCode.ARTIFACT_INTEGRITY_FAILURE, resource=resource)
    except INPUT_ERRORS:
        return WebError(reason_code=WebReasonCode.INVALID_WEB_ARTIFACT, resource=resource)
    # External values are validated; internal projection bugs must remain visible as 500.
    try:
        return _project(state, resource)
    except InvalidArtifact as error:
        return WebError(reason_code=error.reason, resource=resource)
