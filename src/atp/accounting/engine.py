from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeGuard

from atp.accounting.identity import AccountingEntryId, AccountingPolicyId, AccountingReplayId
from atp.accounting.model import (
    AccountingEntry,
    AccountingEntryProvenance,
    AccountingExecution,
    AccountingMark,
    AccountingPosition,
    AccountingPositionStatus,
    AccountingReasonCode,
    AccountingReplayInput,
    AccountingReplayResult,
    AccountingState,
    AccountingStatus,
    AccountingValuation,
)
from atp.accounting.policy import ACCOUNTING_POLICY_V1, AccountingPolicy
from atp.backtesting.identity import SimulatedFillId, SimulatedOrderId, SimulationPolicyId
from atp.backtesting.model import (
    ExecutionProvenance,
    FillProvenance,
    SimulatedFill,
    SimulatedOrderSide,
)
from atp.data.identity import DatasetId, SnapshotId
from atp.data.snapshot import DataFinality, DataQuality, GapStatus
from atp.risk.identity import RiskDecisionId
from atp.risk.model import RiskReasonCode, RiskStatus
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.strategy.identity import StrategyDecisionId, StrategyEvaluationId

_FALLBACK_TIME = datetime(1970, 1, 1, tzinfo=UTC)


class AccountingEngine:
    def __init__(self, policy: AccountingPolicy = ACCOUNTING_POLICY_V1) -> None:
        if not isinstance(policy, AccountingPolicy):
            raise ValidationError("Accounting engine requires the normative V1 policy")
        policy.require_v1()
        self.policy = policy

    def replay(self, replay_input: object) -> AccountingReplayResult:
        invalid = _validate_replay_input(replay_input)
        input_identity = (
            replay_input.content_identity
            if invalid is None and isinstance(replay_input, AccountingReplayInput)
            else _invalid_input_identity(replay_input)
        )
        if invalid is not None or not isinstance(replay_input, AccountingReplayInput):
            return self._blocked(
                reason=invalid or AccountingReasonCode.INVALID_ACCOUNTING_INPUT,
                input_identity=input_identity,
                initial_cash=_safe_initial_cash(replay_input),
                state=AccountingState.initial(_safe_initial_cash(replay_input)),
                ledger=(),
            )

        state = AccountingState.initial(replay_input.initial_cash)
        ledger: list[AccountingEntry] = []
        seen: set[str] = set()
        for execution in replay_input.executions:
            fill = execution.simulated_fill
            fill_id = str(fill.simulated_fill_id)
            if fill_id in seen:
                return self._blocked(
                    AccountingReasonCode.DUPLICATE_FILL,
                    input_identity,
                    replay_input.initial_cash,
                    state,
                    tuple(ledger),
                )
            seen.add(fill_id)
            if state.last_effective_at is not None and fill.fill_time <= state.last_effective_at:
                return self._blocked(
                    AccountingReasonCode.NON_CAUSAL_EXECUTION,
                    input_identity,
                    replay_input.initial_cash,
                    state,
                    tuple(ledger),
                )
            if fill.side is SimulatedOrderSide.BUY_ENTRY:
                if state.position.status is AccountingPositionStatus.OPEN_LONG:
                    return self._blocked(
                        AccountingReasonCode.POSITION_ALREADY_OPEN,
                        input_identity,
                        replay_input.initial_cash,
                        state,
                        tuple(ledger),
                    )
                cost = fill.fill_price * execution.quantity
                if state.cash < cost:
                    return self._blocked(
                        AccountingReasonCode.INSUFFICIENT_CASH,
                        input_identity,
                        replay_input.initial_cash,
                        state,
                        tuple(ledger),
                    )
                next_state = AccountingState(
                    "USDT",
                    state.cash - cost,
                    AccountingPosition.open_long(
                        symbol=fill.symbol,
                        quantity=execution.quantity,
                        average_entry_price=fill.fill_price,
                    ),
                    state.cumulative_realized_pnl,
                    fill.fill_time,
                )
                cash_delta, pnl_delta = -cost, Decimal("0")
            else:
                position = state.position
                if position.status is AccountingPositionStatus.EMPTY:
                    return self._blocked(
                        AccountingReasonCode.NO_OPEN_POSITION,
                        input_identity,
                        replay_input.initial_cash,
                        state,
                        tuple(ledger),
                    )
                if fill.symbol != position.symbol:
                    return self._blocked(
                        AccountingReasonCode.POSITION_SYMBOL_MISMATCH,
                        input_identity,
                        replay_input.initial_cash,
                        state,
                        tuple(ledger),
                    )
                if execution.quantity != position.quantity:
                    return self._blocked(
                        AccountingReasonCode.POSITION_QUANTITY_MISMATCH,
                        input_identity,
                        replay_input.initial_cash,
                        state,
                        tuple(ledger),
                    )
                assert position.average_entry_price is not None
                proceeds = fill.fill_price * execution.quantity
                pnl_delta = (fill.fill_price - position.average_entry_price) * execution.quantity
                next_state = AccountingState(
                    "USDT",
                    state.cash + proceeds,
                    AccountingPosition.empty(),
                    state.cumulative_realized_pnl + pnl_delta,
                    fill.fill_time,
                )
                cash_delta = proceeds
            provenance = AccountingEntryProvenance(
                simulated_fill_id=fill_id,
                simulated_fill_identity=fill.content_identity,
                simulated_order_id=str(fill.simulated_order_id),
                fill_provenance_identity=ContentIdentity.from_canonical(
                    fill.provenance.canonical_value()
                ),
                symbol=fill.symbol,
                side=fill.side,
                quantity=execution.quantity,
                fill_price=fill.fill_price,
                fill_time=fill.fill_time,
                accounting_policy_id=self.policy.policy_id,
                accounting_policy_version=self.policy.version,
                accounting_policy_identity=self.policy.content_identity,
                previous_accounting_state_identity=state.content_identity,
                resulting_accounting_state_identity=next_state.content_identity,
            )
            ledger.append(
                AccountingEntry.create(
                    cash_delta=cash_delta, realized_pnl_delta=pnl_delta, provenance=provenance
                )
            )
            state = next_state
        return AccountingReplayResult.create(
            status=AccountingStatus.COMPLETED,
            reason_code=None,
            input_identity=input_identity,
            accounting_policy_identity=self.policy.content_identity,
            initial_cash=replay_input.initial_cash,
            final_state=state,
            ledger=tuple(ledger),
        )

    def value(
        self, *, replay_result: object, mark: object | None, valuation_time: object
    ) -> AccountingValuation:
        if _valid_replay_result(replay_result, self.policy):
            valid_result: AccountingReplayResult | None = replay_result
            state = replay_result.final_state
        else:
            valid_result = None
            state = AccountingState.initial(Decimal("0"))
        reason: AccountingReasonCode | None = None
        effective_valuation_time = (
            valuation_time
            if isinstance(valuation_time, datetime)
            and valuation_time.tzinfo is not None
            and valuation_time.utcoffset() is not None
            else _FALLBACK_TIME
        )
        if (valid_result is None or valid_result.status is AccountingStatus.BLOCKED) or (
            not isinstance(valuation_time, datetime)
            or valuation_time.tzinfo is None
            or valuation_time.utcoffset() is None
        ):
            reason = AccountingReasonCode.INVALID_ACCOUNTING_INPUT
        elif (
            state.last_effective_at is not None
            and effective_valuation_time < state.last_effective_at
        ):
            reason = AccountingReasonCode.VALUATION_NON_CAUSAL
        elif state.position.status is AccountingPositionStatus.OPEN_LONG:
            reason = _validate_mark(mark, state, effective_valuation_time)
        if reason is not None:
            return _valuation(
                self.policy,
                state,
                AccountingStatus.BLOCKED,
                reason,
                effective_valuation_time,
                None,
                None,
                None,
            )
        if state.position.status is AccountingPositionStatus.EMPTY:
            return _valuation(
                self.policy,
                state,
                AccountingStatus.COMPLETED,
                None,
                effective_valuation_time,
                Decimal("0"),
                state.cash,
                None,
            )
        assert isinstance(mark, AccountingMark)
        assert (
            state.position.quantity is not None and state.position.average_entry_price is not None
        )
        unrealized = (mark.price - state.position.average_entry_price) * state.position.quantity
        equity = state.cash + state.position.quantity * mark.price
        return _valuation(
            self.policy,
            state,
            AccountingStatus.COMPLETED,
            None,
            effective_valuation_time,
            unrealized,
            equity,
            mark.content_identity,
        )

    def _blocked(
        self,
        reason: AccountingReasonCode,
        input_identity: ContentIdentity,
        initial_cash: Decimal,
        state: AccountingState,
        ledger: tuple[AccountingEntry, ...],
    ) -> AccountingReplayResult:
        return AccountingReplayResult.create(
            status=AccountingStatus.BLOCKED,
            reason_code=reason,
            input_identity=input_identity,
            accounting_policy_identity=self.policy.content_identity,
            initial_cash=initial_cash,
            final_state=state,
            ledger=ledger,
        )


def _validate_replay_input(value: object) -> AccountingReasonCode | None:
    if not isinstance(value, AccountingReplayInput):
        return AccountingReasonCode.INVALID_ACCOUNTING_INPUT
    if (
        value.currency != "USDT"
        or not isinstance(value.initial_cash, Decimal)
        or not value.initial_cash.is_finite()
        or value.initial_cash < 0
        or not isinstance(value.executions, tuple)
    ):
        return AccountingReasonCode.INVALID_ACCOUNTING_INPUT
    for execution in value.executions:
        if not isinstance(execution, AccountingExecution):
            return AccountingReasonCode.INVALID_ACCOUNTING_INPUT
        if (
            not isinstance(execution.quantity, Decimal)
            or not execution.quantity.is_finite()
            or execution.quantity <= 0
        ):
            return AccountingReasonCode.INVALID_QUANTITY
        if not isinstance(execution.simulated_fill, SimulatedFill):
            return AccountingReasonCode.INVALID_FILL
        reason = _validate_fill(execution.simulated_fill)
        if reason is not None:
            return reason
    return None


def _validate_fill(fill: SimulatedFill) -> AccountingReasonCode | None:
    if (
        not isinstance(fill.simulated_fill_id, SimulatedFillId)
        or not isinstance(fill.simulated_order_id, SimulatedOrderId)
        or not isinstance(fill.symbol, str)
        or not fill.symbol
        or not isinstance(fill.side, SimulatedOrderSide)
        or not isinstance(fill.fill_price, Decimal)
        or not fill.fill_price.is_finite()
        or fill.fill_price <= 0
    ):
        return AccountingReasonCode.INVALID_FILL
    if (
        not isinstance(fill.fill_time, datetime)
        or fill.fill_time.tzinfo is None
        or fill.fill_time.utcoffset() is None
        or not isinstance(fill.source_bar_identity, ContentIdentity)
        or not isinstance(fill.simulation_policy_id, SimulationPolicyId)
        or not isinstance(fill.simulation_policy_version, str)
        or not isinstance(fill.content_identity, ContentIdentity)
    ):
        return AccountingReasonCode.INVALID_FILL
    if not isinstance(fill.provenance, FillProvenance):
        return AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE
    provenance = fill.provenance
    order = provenance.order_provenance
    if (
        not isinstance(order, ExecutionProvenance)
        or not isinstance(provenance.simulated_order_identity, ContentIdentity)
        or not isinstance(provenance.source_bar_identity, ContentIdentity)
        or not _aware_datetime(provenance.source_bar_event_time)
        or not _aware_datetime(provenance.source_bar_available_at)
        or not isinstance(order.dataset_id, DatasetId)
        or not isinstance(order.snapshot_id, SnapshotId)
        or not isinstance(order.snapshot_identity, ContentIdentity)
        or not isinstance(order.evaluation_bar_identity, ContentIdentity)
        or not _aware_datetime(order.evaluation_bar_event_time)
        or not _aware_datetime(order.evaluation_time)
        or not isinstance(order.strategy_evaluation_id, StrategyEvaluationId)
        or not isinstance(order.strategy_evaluation_identity, ContentIdentity)
        or not isinstance(order.strategy_decision_id, StrategyDecisionId)
        or not isinstance(order.strategy_signal_identity, ContentIdentity)
        or not isinstance(order.risk_decision_id, RiskDecisionId)
        or not isinstance(order.risk_decision_identity, ContentIdentity)
        or not isinstance(order.risk_status, RiskStatus)
        or not isinstance(order.risk_reason_code, RiskReasonCode)
        or not isinstance(order.simulation_policy_id, SimulationPolicyId)
        or not isinstance(order.simulation_policy_version, str)
        or not isinstance(order.simulation_policy_identity, ContentIdentity)
        or not isinstance(order.symbol, str)
    ):
        return AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE
    try:
        if (
            fill.source_bar_identity != fill.provenance.source_bar_identity
            or fill.fill_time != fill.provenance.source_bar_available_at
            or fill.provenance.source_bar_event_time > fill.fill_time
            or fill.provenance.order_provenance.evaluation_time > fill.fill_time
            or fill.symbol != fill.provenance.order_provenance.symbol
            or fill.simulation_policy_id != fill.provenance.order_provenance.simulation_policy_id
            or fill.simulation_policy_version
            != fill.provenance.order_provenance.simulation_policy_version
            or fill.provenance.order_provenance.risk_status is not RiskStatus.APPROVED
        ):
            return AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE
        expected = ContentIdentity.from_canonical(
            {
                "fill_price": str(fill.fill_price),
                "fill_time": fill.fill_time.isoformat(),
                "provenance": fill.provenance.canonical_value(),
                "side": fill.side.value,
                "simulated_order_id": str(fill.simulated_order_id),
                "symbol": fill.symbol,
            }
        )
        if (
            expected != fill.content_identity
            or str(fill.simulated_fill_id) != f"simulated-fill:{expected}"
        ):
            return AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE
    except (AttributeError, TypeError, ValueError):
        return AccountingReasonCode.FILL_PROVENANCE_INCOMPATIBLE
    return None


def _validate_mark(
    mark: object | None, state: AccountingState, valuation_time: datetime
) -> AccountingReasonCode | None:
    if mark is None:
        return AccountingReasonCode.MARK_REQUIRED
    if not isinstance(mark, AccountingMark):
        return AccountingReasonCode.MARK_INADMISSIBLE
    if (
        not isinstance(mark.symbol, str)
        or not mark.symbol
        or not isinstance(mark.event_time, datetime)
        or mark.event_time.tzinfo is None
        or mark.event_time.utcoffset() is None
        or not isinstance(mark.available_at, datetime)
        or mark.available_at.tzinfo is None
        or mark.available_at.utcoffset() is None
        or not isinstance(mark.price, Decimal)
        or not mark.price.is_finite()
        or mark.price <= 0
        or mark.finality is not DataFinality.FINAL
        or mark.validation_as_of_use is not DataQuality.VALID
        or mark.gap_status is not GapStatus.NO_GAP_DETECTED
        or mark.symbol != state.position.symbol
    ):
        return AccountingReasonCode.MARK_INADMISSIBLE
    if mark.available_at > valuation_time or mark.event_time > valuation_time:
        return AccountingReasonCode.VALUATION_NON_CAUSAL
    return None


def _safe_initial_cash(value: object) -> Decimal:
    cash = value.initial_cash if isinstance(value, AccountingReplayInput) else None
    return cash if isinstance(cash, Decimal) and cash.is_finite() and cash >= 0 else Decimal("0")


def _valid_replay_result(
    value: object, policy: AccountingPolicy
) -> TypeGuard[AccountingReplayResult]:
    if not isinstance(value, AccountingReplayResult):
        return False
    state = value.final_state
    if (
        not isinstance(value.status, AccountingStatus)
        or not isinstance(value.reason_code, AccountingReasonCode | None)
        or not isinstance(value.accounting_replay_id, AccountingReplayId)
        or not isinstance(value.input_identity, ContentIdentity)
        or not isinstance(value.accounting_policy_identity, ContentIdentity)
        or value.accounting_policy_identity != policy.content_identity
        or not isinstance(value.initial_cash, Decimal)
        or not value.initial_cash.is_finite()
        or value.initial_cash < 0
        or not isinstance(state, AccountingState)
        or state.currency != "USDT"
        or not isinstance(state.cash, Decimal)
        or not state.cash.is_finite()
        or state.cash < 0
        or not isinstance(state.cumulative_realized_pnl, Decimal)
        or not state.cumulative_realized_pnl.is_finite()
        or not isinstance(state.position, AccountingPosition)
        or not isinstance(value.ledger, tuple)
        or not isinstance(value.content_identity, ContentIdentity)
        or (state.last_effective_at is not None and not _aware_datetime(state.last_effective_at))
    ):
        return False
    if (value.status is AccountingStatus.COMPLETED) != (value.reason_code is None):
        return False
    position = state.position
    if not isinstance(position.status, AccountingPositionStatus):
        return False
    if position.status is AccountingPositionStatus.EMPTY:
        position_valid = (
            position.symbol is None
            and position.quantity is None
            and position.average_entry_price is None
        )
    else:
        position_valid = (
            isinstance(position.symbol, str)
            and bool(position.symbol)
            and isinstance(position.quantity, Decimal)
            and position.quantity.is_finite()
            and position.quantity > 0
            and isinstance(position.average_entry_price, Decimal)
            and position.average_entry_price.is_finite()
            and position.average_entry_price > 0
        )
    if not position_valid or not _ledger_matches_result(value, policy):
        return False
    canonical = {
        "accounting_policy_identity": str(value.accounting_policy_identity),
        "final_state": state.canonical_value(),
        "initial_cash": str(value.initial_cash),
        "input_identity": str(value.input_identity),
        "ledger": [str(entry.content_identity) for entry in value.ledger],
        "reason_code": None if value.reason_code is None else value.reason_code.value,
        "status": value.status.value,
    }
    expected = ContentIdentity.from_canonical(canonical)
    return value.content_identity == expected and value.accounting_replay_id == AccountingReplayId(
        f"accounting-replay:{expected}"
    )


def _ledger_matches_result(value: AccountingReplayResult, policy: AccountingPolicy) -> bool:
    state = AccountingState.initial(value.initial_cash)
    seen_fill_ids: set[str] = set()
    for entry in value.ledger:
        if not _valid_entry(entry, policy):
            return False
        provenance = entry.provenance
        if (
            provenance.previous_accounting_state_identity != state.content_identity
            or provenance.simulated_fill_id in seen_fill_ids
            or (
                state.last_effective_at is not None
                and provenance.fill_time <= state.last_effective_at
            )
        ):
            return False
        seen_fill_ids.add(provenance.simulated_fill_id)
        if provenance.side is SimulatedOrderSide.BUY_ENTRY:
            if state.position.status is not AccountingPositionStatus.EMPTY:
                return False
            expected_cash_delta = -(provenance.fill_price * provenance.quantity)
            expected_pnl_delta = Decimal("0")
            next_position = AccountingPosition.open_long(
                symbol=provenance.symbol,
                quantity=provenance.quantity,
                average_entry_price=provenance.fill_price,
            )
        else:
            position = state.position
            if (
                position.status is not AccountingPositionStatus.OPEN_LONG
                or position.symbol != provenance.symbol
                or position.quantity != provenance.quantity
                or position.average_entry_price is None
            ):
                return False
            expected_cash_delta = provenance.fill_price * provenance.quantity
            expected_pnl_delta = (
                provenance.fill_price - position.average_entry_price
            ) * provenance.quantity
            next_position = AccountingPosition.empty()
        if (
            entry.cash_delta != expected_cash_delta
            or entry.realized_pnl_delta != expected_pnl_delta
            or state.cash + expected_cash_delta < 0
        ):
            return False
        state = AccountingState(
            currency="USDT",
            cash=state.cash + expected_cash_delta,
            position=next_position,
            cumulative_realized_pnl=state.cumulative_realized_pnl + expected_pnl_delta,
            last_effective_at=provenance.fill_time,
        )
        if provenance.resulting_accounting_state_identity != state.content_identity:
            return False
    return state == value.final_state


def _valid_entry(entry: object, policy: AccountingPolicy) -> TypeGuard[AccountingEntry]:
    if not isinstance(entry, AccountingEntry) or not isinstance(
        entry.provenance, AccountingEntryProvenance
    ):
        return False
    provenance = entry.provenance
    if (
        not isinstance(entry.accounting_entry_id, AccountingEntryId)
        or not isinstance(entry.cash_delta, Decimal)
        or not entry.cash_delta.is_finite()
        or not isinstance(entry.realized_pnl_delta, Decimal)
        or not entry.realized_pnl_delta.is_finite()
        or not isinstance(entry.content_identity, ContentIdentity)
        or not isinstance(provenance.simulated_fill_id, str)
        or not provenance.simulated_fill_id
        or not isinstance(provenance.simulated_fill_identity, ContentIdentity)
        or not isinstance(provenance.simulated_order_id, str)
        or not provenance.simulated_order_id
        or not isinstance(provenance.fill_provenance_identity, ContentIdentity)
        or not isinstance(provenance.symbol, str)
        or not provenance.symbol
        or not isinstance(provenance.side, SimulatedOrderSide)
        or not isinstance(provenance.quantity, Decimal)
        or not provenance.quantity.is_finite()
        or provenance.quantity <= 0
        or not isinstance(provenance.fill_price, Decimal)
        or not provenance.fill_price.is_finite()
        or provenance.fill_price <= 0
        or not _aware_datetime(provenance.fill_time)
        or not isinstance(provenance.accounting_policy_id, AccountingPolicyId)
        or provenance.accounting_policy_id != policy.policy_id
        or provenance.accounting_policy_version != policy.version
        or provenance.accounting_policy_identity != policy.content_identity
        or not isinstance(provenance.previous_accounting_state_identity, ContentIdentity)
        or not isinstance(provenance.resulting_accounting_state_identity, ContentIdentity)
    ):
        return False
    expected = ContentIdentity.from_canonical(
        {
            "cash_delta": str(entry.cash_delta),
            "provenance": provenance.canonical_value(),
            "realized_pnl_delta": str(entry.realized_pnl_delta),
        }
    )
    return entry.content_identity == expected and entry.accounting_entry_id == AccountingEntryId(
        f"accounting-entry:{expected}"
    )


def _invalid_input_identity(value: object) -> ContentIdentity:
    value_type = type(value)
    return ContentIdentity.from_canonical(
        {"invalid_runtime_type": f"{value_type.__module__}.{value_type.__qualname__}"}
    )


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _valuation(
    policy: AccountingPolicy,
    state: AccountingState,
    status: AccountingStatus,
    reason: AccountingReasonCode | None,
    valuation_time: datetime,
    unrealized: Decimal | None,
    equity: Decimal | None,
    mark_identity: ContentIdentity | None,
) -> AccountingValuation:
    return AccountingValuation.create(
        status=status,
        reason_code=reason,
        cash=state.cash,
        position=state.position,
        realized_pnl=state.cumulative_realized_pnl,
        unrealized_pnl=unrealized,
        equity=equity,
        mark_identity=mark_identity,
        valuation_time=valuation_time,
        accounting_state_identity=state.content_identity,
        accounting_policy_identity=policy.content_identity,
    )
