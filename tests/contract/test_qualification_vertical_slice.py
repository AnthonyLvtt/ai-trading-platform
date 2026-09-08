from datetime import UTC, datetime
from decimal import Decimal

from atp.accounting import AccountingEngine, AccountingExecution, AccountingReplayInput
from atp.observability import (
    AuditJournal,
    ObservabilityStatus,
    ObservationContext,
    observe_accounting_entry,
    observe_simulated_fill,
)
from atp.shared.environment import Environment
from atp.shared.identity import CausationId, CorrelationId
from atp.shared.serialization import canonical_json_bytes
from atp.test_qualification import (
    CASES_V1,
    SUITE_V1,
    QualificationStatus,
    QualificationSubject,
    build_evidence,
    evaluate_case,
    evaluate_suite,
)
from tests.contract.test_observability_vertical_slice import _completed_backtest
from tests.unit import test_backtesting_engine as backtest_probes


def test_real_vertical_slice_produces_bound_qualifiable_evidence():
    snapshot, replay_input, replay = _completed_backtest(
        snapshot_created_at=datetime(2026, 9, 1, tzinfo=UTC)
    )
    _, _, repeated = _completed_backtest(snapshot_created_at=snapshot.created_at)
    fill = replay.steps[0].fill
    order = replay.steps[0].order
    assert fill is not None and order is not None
    accounting = AccountingEngine().replay(
        AccountingReplayInput(
            initial_cash=Decimal("100"),
            currency="USDT",
            executions=(AccountingExecution(fill, Decimal("2")),),
        )
    )
    correlation = CorrelationId("qualification-contract")
    journal = AuditJournal.empty()
    observed_fill = observe_simulated_fill(
        fill, environment=Environment.BACKTEST, context=ObservationContext(correlation)
    )
    assert observed_fill.event is not None
    appended = journal.append(observed_fill.event)
    assert appended.journal is not None
    journal = appended.journal
    observed_entry = observe_accounting_entry(
        accounting.ledger[0],
        environment=Environment.BACKTEST,
        context=ObservationContext(
            correlation,
            CausationId(str(observed_fill.event.event_id)),
            observed_fill.event.content_identity,
        ),
    )
    assert observed_entry.event is not None
    appended = journal.append(observed_entry.event)
    assert appended.journal is not None
    journal = appended.journal
    assert journal.validate().status is ObservabilityStatus.ACCEPTED

    # Exercise adverse paths rather than declaring them satisfied from a happy path fill.
    backtest_probes.test_no_action_and_non_approved_risk_never_create_orders()
    backtest_probes.test_non_final_next_bar_blocks_without_using_later_bar()
    backtest_probes.test_delayed_fill_state_cannot_leak_into_earlier_following_evaluation()
    backtest_probes.test_end_of_replay_leaves_approved_order_unfilled()
    case = next(c for c in CASES_V1 if c.case_id == "Q-BACKTEST-001")
    checks = {
        "risk_gate": order.risk_decision_id
        == replay_input.steps[0].risk_result.decision.risk_decision_id,
        "next_open": fill.fill_price == Decimal("4.5"),
        "no_current_close": fill.fill_time > order.created_at and fill.fill_price != Decimal("4"),
        "no_skip": True,
        "no_future_state": True,
        "end_unfilled": True,
        "determinism": replay == repeated,
    }
    subject = QualificationSubject(
        "vertical-slice",
        "backtesting",
        "vertical-contract-v1",
        canonical_json_bytes(
            {
                "observations": {
                    check: {
                        "case_id": case.case_id,
                        "check_id": check,
                        "satisfied": passed,
                        "reproducible": True,
                        "applicable": True,
                    }
                    for check, passed in checks.items()
                },
                "references": {
                    "data": str(snapshot.content_identity),
                    "strategy": str(replay_input.steps[0].strategy_evaluation.content_identity),
                    "risk": str(replay_input.steps[0].risk_result.content_identity),
                    "backtest": str(replay.content_identity),
                    "fill": str(fill.content_identity),
                    "accounting": str(accounting.content_identity),
                    "audit": str(journal.content_identity),
                },
            }
        ),
    )
    evidence = tuple(
        build_evidence(
            evidence_id=f"vertical:{check}",
            case=case,
            check_id=check,
            subject=subject,
            payload={
                "observation_id": check,
                "satisfied": passed,
                "applicable": True,
                "reproducible": True,
            },
        ).evidence
        for check, passed in checks.items()
    )
    qualified = evaluate_case(case, evidence, subjects=(subject,))
    assert qualified.status is QualificationStatus.PASSED
    assert qualified.evaluated_evidence_identities == tuple(
        e.content_identity for e in sorted(evidence, key=lambda e: e.evidence_id)
    )
    assert (
        evaluate_suite(SUITE_V1, (case,), evidence, subjects=(subject,)).status
        is QualificationStatus.BLOCKED
    )


def test_real_slice_valuation_identity_rejects_tampering():
    from atp.accounting import AccountingMark, AccountingStatus
    from atp.observability import observe_accounting_valuation

    snapshot, _, replay = _completed_backtest(snapshot_created_at=datetime(2026, 9, 1, tzinfo=UTC))
    fill = replay.steps[0].fill
    assert fill is not None
    accounting = AccountingEngine().replay(
        AccountingReplayInput(
            initial_cash=Decimal("100"),
            currency="USDT",
            executions=(AccountingExecution(fill, Decimal("2")),),
        )
    )
    mark = AccountingMark.from_data(point=snapshot.points[-1], snapshot=snapshot)
    valuation = AccountingEngine().value(
        replay_result=accounting, mark=mark, valuation_time=fill.fill_time
    )
    assert valuation.status is AccountingStatus.COMPLETED
    context = ObservationContext(CorrelationId("qualification-valuation"))
    accepted = observe_accounting_valuation(
        valuation, environment=Environment.BACKTEST, context=context
    )
    assert accepted.status is ObservabilityStatus.ACCEPTED
    assert accepted.event is not None
    assert accepted.event.subject_content_identity == valuation.content_identity
    object.__setattr__(valuation, "equity", Decimal("999"))
    blocked = observe_accounting_valuation(
        valuation, environment=Environment.BACKTEST, context=context
    )
    assert blocked.status is ObservabilityStatus.BLOCKED
