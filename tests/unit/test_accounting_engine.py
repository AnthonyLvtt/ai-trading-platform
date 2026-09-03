from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from atp.accounting import (
    ACCOUNTING_POLICY_V1,
    AccountingEngine,
    AccountingExecution,
    AccountingMark,
    AccountingPositionStatus,
    AccountingReasonCode,
    AccountingReplayInput,
    AccountingStatus,
)
from atp.backtesting.identity import SimulatedFillId
from atp.backtesting.model import SimulatedFill
from atp.shared.identity import ContentIdentity
from tests.unit.test_backtesting_engine import (
    empty_portfolio,
    open_portfolio,
    replay,
    replay_step,
    snapshot,
)


def fills() -> tuple[SimulatedFill, SimulatedFill]:
    data = snapshot()
    result = replay(
        data,
        replay_step(data, 3, empty_portfolio()),
        replay_step(data, 5, open_portfolio()),
    )
    entry = result.steps[0].fill
    exit_fill = result.steps[1].fill
    assert entry is not None and exit_fill is not None
    return entry, exit_fill


def accounting_input(*executions: AccountingExecution) -> AccountingReplayInput:
    return AccountingReplayInput(Decimal("100"), "USDT", tuple(executions))


def rebind_fill(fill: SimulatedFill, symbol: str) -> SimulatedFill:
    order_provenance = replace(fill.provenance.order_provenance, symbol=symbol)
    provenance = replace(fill.provenance, order_provenance=order_provenance)
    canonical = {
        "fill_price": str(fill.fill_price),
        "fill_time": fill.fill_time.isoformat(),
        "provenance": provenance.canonical_value(),
        "side": fill.side.value,
        "simulated_order_id": str(fill.simulated_order_id),
        "symbol": symbol,
    }
    identity = ContentIdentity.from_canonical(canonical)
    return replace(
        fill,
        simulated_fill_id=SimulatedFillId(f"simulated-fill:{identity}"),
        symbol=symbol,
        provenance=provenance,
        content_identity=identity,
    )


def retime_fill(fill: SimulatedFill, fill_time: datetime) -> SimulatedFill:
    provenance = replace(
        fill.provenance,
        source_bar_event_time=fill_time,
        source_bar_available_at=fill_time,
    )
    canonical = {
        "fill_price": str(fill.fill_price),
        "fill_time": fill_time.isoformat(),
        "provenance": provenance.canonical_value(),
        "side": fill.side.value,
        "simulated_order_id": str(fill.simulated_order_id),
        "symbol": fill.symbol,
    }
    identity = ContentIdentity.from_canonical(canonical)
    return replace(
        fill,
        simulated_fill_id=SimulatedFillId(f"simulated-fill:{identity}"),
        fill_time=fill_time,
        provenance=provenance,
        content_identity=identity,
    )


def test_policy_encodes_normative_v1_configuration() -> None:
    value = ACCOUNTING_POLICY_V1.canonical_value()

    assert str(ACCOUNTING_POLICY_V1.policy_id) == "ATP_ACCOUNTING_V1"
    assert ACCOUNTING_POLICY_V1.version == "1.0"
    assert value["quantity_source"] == "EXTERNAL_FACT"
    assert value["fees"] == "0"
    assert value["business_rounding"] is None


def test_buy_entry_records_cash_position_and_immutable_ledger() -> None:
    entry, _ = fills()
    result = AccountingEngine().replay(accounting_input(AccountingExecution(entry, Decimal("2"))))

    assert result.status is AccountingStatus.COMPLETED
    assert result.final_state.cash == Decimal("91.0")
    assert result.final_state.position.status is AccountingPositionStatus.OPEN_LONG
    assert result.final_state.position.symbol == "BTCUSDT"
    assert result.final_state.position.quantity == Decimal("2")
    assert result.final_state.position.average_entry_price == Decimal("4.5")
    assert result.ledger[0].cash_delta == Decimal("-9.0")
    assert result.ledger[0].provenance.simulated_fill_id == str(entry.simulated_fill_id)


def test_sell_exit_realizes_pnl_and_returns_to_empty() -> None:
    entry, exit_fill = fills()
    result = AccountingEngine().replay(
        accounting_input(
            AccountingExecution(entry, Decimal("2")),
            AccountingExecution(exit_fill, Decimal("2")),
        )
    )

    assert result.status is AccountingStatus.COMPLETED
    assert result.final_state.cash == Decimal("92.0")
    assert result.final_state.cumulative_realized_pnl == Decimal("-8.0")
    assert result.final_state.position.status is AccountingPositionStatus.EMPTY
    assert result.ledger[1].realized_pnl_delta == Decimal("-8.0")


def test_replay_is_deterministic() -> None:
    entry, exit_fill = fills()
    value = accounting_input(
        AccountingExecution(entry, Decimal("2")),
        AccountingExecution(exit_fill, Decimal("2")),
    )

    first = AccountingEngine().replay(value)
    second = AccountingEngine().replay(value)

    assert first == second
    assert first.content_identity == second.content_identity
    assert first.ledger == second.ledger


def test_insufficient_cash_is_blocked_without_entry() -> None:
    entry, _ = fills()
    result = AccountingEngine().replay(
        AccountingReplayInput(Decimal("1"), "USDT", (AccountingExecution(entry, Decimal("2")),))
    )

    assert result.status is AccountingStatus.BLOCKED
    assert result.reason_code is AccountingReasonCode.INSUFFICIENT_CASH
    assert result.ledger == ()
    assert result.final_state.cash == Decimal("1")


def test_duplicate_fill_is_blocked_without_silent_deduplication() -> None:
    entry, _ = fills()
    execution = AccountingExecution(entry, Decimal("2"))
    result = AccountingEngine().replay(accounting_input(execution, execution))

    assert result.status is AccountingStatus.BLOCKED
    assert result.reason_code is AccountingReasonCode.DUPLICATE_FILL
    assert len(result.ledger) == 1


def test_wrong_exit_symbol_is_blocked() -> None:
    entry, exit_fill = fills()
    exit_fill = rebind_fill(exit_fill, "ETHUSDT")
    result = AccountingEngine().replay(
        accounting_input(
            AccountingExecution(entry, Decimal("2")),
            AccountingExecution(exit_fill, Decimal("2")),
        )
    )

    assert result.status is AccountingStatus.BLOCKED
    assert result.reason_code is AccountingReasonCode.POSITION_SYMBOL_MISMATCH


def test_wrong_exit_quantity_is_blocked() -> None:
    entry, exit_fill = fills()
    result = AccountingEngine().replay(
        accounting_input(
            AccountingExecution(entry, Decimal("2")),
            AccountingExecution(exit_fill, Decimal("1")),
        )
    )

    assert result.reason_code is AccountingReasonCode.POSITION_QUANTITY_MISMATCH
    assert len(result.ledger) == 1


def test_exit_without_position_is_blocked() -> None:
    _, exit_fill = fills()
    result = AccountingEngine().replay(
        accounting_input(AccountingExecution(exit_fill, Decimal("2")))
    )

    assert result.reason_code is AccountingReasonCode.NO_OPEN_POSITION
    assert result.ledger == ()


def test_non_causal_execution_sequence_is_blocked() -> None:
    entry, exit_fill = fills()
    entry = retime_fill(entry, exit_fill.fill_time)
    result = AccountingEngine().replay(
        accounting_input(
            AccountingExecution(entry, Decimal("2")),
            AccountingExecution(exit_fill, Decimal("2")),
        )
    )

    assert result.reason_code is AccountingReasonCode.NON_CAUSAL_EXECUTION
    assert len(result.ledger) == 1


@pytest.mark.parametrize("quantity", ["2", 2, Decimal("NaN"), Decimal("0"), Decimal("-1")])
def test_invalid_quantity_fails_closed(quantity: object) -> None:
    entry, _ = fills()
    execution = AccountingExecution(entry, Decimal("2"))
    object.__setattr__(execution, "quantity", quantity)

    result = AccountingEngine().replay(accounting_input(execution))

    assert result.status is AccountingStatus.BLOCKED
    assert result.reason_code is AccountingReasonCode.INVALID_QUANTITY


@pytest.mark.parametrize("bad_input", [None, "input", [], object()])
def test_malformed_top_level_input_fails_closed_deterministically(bad_input: object) -> None:
    first = AccountingEngine().replay(bad_input)
    second = AccountingEngine().replay(bad_input)

    assert first == second
    assert first.status is AccountingStatus.BLOCKED
    assert first.reason_code is AccountingReasonCode.INVALID_ACCOUNTING_INPUT


def test_corrupt_execution_and_fill_fail_closed() -> None:
    entry, _ = fills()
    value = accounting_input(AccountingExecution(entry, Decimal("2")))
    object.__setattr__(value, "executions", (object(),))
    assert (
        AccountingEngine().replay(value).reason_code
        is AccountingReasonCode.INVALID_ACCOUNTING_INPUT
    )

    value = accounting_input(AccountingExecution(entry, Decimal("2")))
    object.__setattr__(value.executions[0], "simulated_fill", object())
    assert AccountingEngine().replay(value).reason_code is AccountingReasonCode.INVALID_FILL


def test_corrupt_fill_provenance_fails_closed() -> None:
    entry, _ = fills()
    object.__setattr__(entry, "provenance", object())

    result = AccountingEngine().replay(accounting_input(AccountingExecution(entry, Decimal("2"))))

    assert result.reason_code is AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE


def test_non_approved_risk_provenance_cannot_modify_accounting() -> None:
    from atp.risk.model import RiskStatus

    entry, _ = fills()
    order_provenance = replace(entry.provenance.order_provenance, risk_status=RiskStatus.BLOCKED)
    object.__setattr__(entry.provenance, "order_provenance", order_provenance)

    result = AccountingEngine().replay(accounting_input(AccountingExecution(entry, Decimal("2"))))

    assert result.reason_code is AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE
    assert result.ledger == ()


def test_open_position_requires_causal_admissible_mark_and_equity_invariant() -> None:
    data = snapshot()
    entry, _ = fills()
    replay_result = AccountingEngine().replay(
        accounting_input(AccountingExecution(entry, Decimal("2")))
    )
    mark = AccountingMark.from_data(point=data.points[6], snapshot=data)
    valuation = AccountingEngine().value(
        replay_result=replay_result,
        mark=mark,
        valuation_time=data.points[6].temporal.available_at,
    )

    assert valuation.status is AccountingStatus.COMPLETED
    assert valuation.unrealized_pnl == Decimal("-7.0")
    assert valuation.equity == Decimal("93.0")
    assert valuation.equity == (
        replay_result.initial_cash + valuation.realized_pnl + valuation.unrealized_pnl
    )


def test_mark_is_required_for_open_position() -> None:
    entry, _ = fills()
    replay_result = AccountingEngine().replay(
        accounting_input(AccountingExecution(entry, Decimal("2")))
    )

    valuation = AccountingEngine().value(
        replay_result=replay_result, mark=None, valuation_time=entry.fill_time
    )

    assert valuation.status is AccountingStatus.BLOCKED
    assert valuation.reason_code is AccountingReasonCode.MARK_REQUIRED
    assert valuation.equity is None


def test_future_mark_is_non_causal() -> None:
    data = snapshot()
    entry, _ = fills()
    replay_result = AccountingEngine().replay(
        accounting_input(AccountingExecution(entry, Decimal("2")))
    )
    mark = AccountingMark.from_data(point=data.points[6], snapshot=data)

    valuation = AccountingEngine().value(
        replay_result=replay_result,
        mark=mark,
        valuation_time=mark.available_at - timedelta(seconds=1),
    )

    assert valuation.reason_code is AccountingReasonCode.VALUATION_NON_CAUSAL


def test_invalid_and_malformed_marks_fail_closed() -> None:
    data = snapshot()
    entry, _ = fills()
    replay_result = AccountingEngine().replay(
        accounting_input(AccountingExecution(entry, Decimal("2")))
    )
    mark = AccountingMark.from_data(point=data.points[6], snapshot=data)
    object.__setattr__(mark, "price", "1")

    invalid = AccountingEngine().value(
        replay_result=replay_result,
        mark=mark,
        valuation_time=data.points[6].temporal.available_at,
    )
    malformed = AccountingEngine().value(
        replay_result=replay_result,
        mark=object(),
        valuation_time=data.points[6].temporal.available_at,
    )

    assert invalid.reason_code is AccountingReasonCode.MARK_INADMISSIBLE
    assert malformed.reason_code is AccountingReasonCode.MARK_INADMISSIBLE


def test_empty_state_valuation_needs_no_mark() -> None:
    replay_result = AccountingEngine().replay(accounting_input())
    valuation = AccountingEngine().value(
        replay_result=replay_result,
        mark=None,
        valuation_time=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert valuation.status is AccountingStatus.COMPLETED
    assert valuation.unrealized_pnl == Decimal("0")
    assert valuation.equity == Decimal("100")


def test_malformed_replay_result_and_state_fail_closed_during_valuation() -> None:
    valid = AccountingEngine().replay(accounting_input())
    object.__setattr__(valid, "final_state", object())

    first = AccountingEngine().value(
        replay_result=valid,
        mark=None,
        valuation_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = AccountingEngine().value(
        replay_result=object(),
        mark=None,
        valuation_time=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert first.status is AccountingStatus.BLOCKED
    assert first.reason_code is AccountingReasonCode.INVALID_ACCOUNTING_INPUT
    assert second.status is AccountingStatus.BLOCKED


def test_policy_identity_changes_replay_identity() -> None:
    entry, _ = fills()
    value = accounting_input(AccountingExecution(entry, Decimal("2")))
    first = AccountingEngine().replay(value)
    different_policy = replace(ACCOUNTING_POLICY_V1, version="1.1")
    second = AccountingEngine(different_policy).replay(value)

    assert first.content_identity != second.content_identity
    assert first.accounting_policy_identity != second.accounting_policy_identity
