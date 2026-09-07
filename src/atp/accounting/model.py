from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from atp.accounting.identity import (
    AccountingEntryId,
    AccountingPolicyId,
    AccountingReplayId,
    AccountingValuationId,
)
from atp.backtesting.model import SimulatedFill, SimulatedOrderSide
from atp.data.snapshot import DataFinality, DataPoint, DataQuality, DatasetSnapshot, GapStatus
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import require_utc


class AccountingStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class AccountingReasonCode(StrEnum):
    INVALID_ACCOUNTING_INPUT = "INVALID_ACCOUNTING_INPUT"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_FILL = "INVALID_FILL"
    FILL_PROVENANCE_INCOMPATIBLE = "FILL_PROVENANCE_INCOMPATIBLE"
    DUPLICATE_FILL = "DUPLICATE_FILL"
    NON_CAUSAL_EXECUTION = "NON_CAUSAL_EXECUTION"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    NO_OPEN_POSITION = "NO_OPEN_POSITION"
    POSITION_SYMBOL_MISMATCH = "POSITION_SYMBOL_MISMATCH"
    POSITION_QUANTITY_MISMATCH = "POSITION_QUANTITY_MISMATCH"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    ACCOUNTING_STATE_INCONSISTENT = "ACCOUNTING_STATE_INCONSISTENT"
    MARK_REQUIRED = "MARK_REQUIRED"
    MARK_INADMISSIBLE = "MARK_INADMISSIBLE"
    VALUATION_NON_CAUSAL = "VALUATION_NON_CAUSAL"


class AccountingPositionStatus(StrEnum):
    EMPTY = "EMPTY"
    OPEN_LONG = "OPEN_LONG"


@dataclass(frozen=True, slots=True)
class AccountingPosition:
    status: AccountingPositionStatus
    symbol: str | None
    quantity: Decimal | None
    average_entry_price: Decimal | None

    @classmethod
    def empty(cls) -> AccountingPosition:
        return cls(AccountingPositionStatus.EMPTY, None, None, None)

    @classmethod
    def open_long(
        cls, *, symbol: str, quantity: Decimal, average_entry_price: Decimal
    ) -> AccountingPosition:
        return cls(AccountingPositionStatus.OPEN_LONG, symbol, quantity, average_entry_price)

    def __post_init__(self) -> None:
        if self.status is AccountingPositionStatus.EMPTY:
            if any(
                value is not None
                for value in (self.symbol, self.quantity, self.average_entry_price)
            ):
                raise ValidationError("EMPTY accounting position cannot carry economic values")
            return
        if (
            not isinstance(self.symbol, str)
            or not self.symbol
            or self.symbol.strip() != self.symbol
        ):
            raise ValidationError("OPEN_LONG requires a trimmed symbol")
        for name, value in (
            ("quantity", self.quantity),
            ("average_entry_price", self.average_entry_price),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValidationError(f"OPEN_LONG requires a positive finite {name}")

    def canonical_value(self) -> dict[str, object]:
        return {
            "average_entry_price": None
            if self.average_entry_price is None
            else str(self.average_entry_price),
            "quantity": None if self.quantity is None else str(self.quantity),
            "status": self.status.value,
            "symbol": self.symbol,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class AccountingState:
    currency: str
    cash: Decimal
    position: AccountingPosition
    cumulative_realized_pnl: Decimal
    last_effective_at: datetime | None

    @classmethod
    def initial(cls, initial_cash: Decimal) -> AccountingState:
        return cls("USDT", initial_cash, AccountingPosition.empty(), Decimal("0"), None)

    def __post_init__(self) -> None:
        if self.currency != "USDT":
            raise ValidationError("Accounting V1 currency must be USDT")
        for name, value in (
            ("cash", self.cash),
            ("cumulative_realized_pnl", self.cumulative_realized_pnl),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValidationError(f"Accounting {name} must be a finite Decimal")
        if self.cash < 0:
            raise ValidationError("Accounting cash cannot be negative")
        if not isinstance(self.position, AccountingPosition):
            raise ValidationError("Accounting state requires a valid position")
        if self.last_effective_at is not None:
            require_utc(self.last_effective_at)

    def canonical_value(self) -> dict[str, object]:
        return {
            "cash": str(self.cash),
            "cumulative_realized_pnl": str(self.cumulative_realized_pnl),
            "currency": self.currency,
            "last_effective_at": None
            if self.last_effective_at is None
            else self.last_effective_at.isoformat(),
            "position": self.position.canonical_value(),
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class AccountingExecution:
    simulated_fill: SimulatedFill
    quantity: Decimal

    def canonical_value(self) -> dict[str, str]:
        return {
            "quantity": str(self.quantity),
            "simulated_fill_identity": str(self.simulated_fill.content_identity),
        }


@dataclass(frozen=True, slots=True)
class AccountingReplayInput:
    initial_cash: Decimal
    currency: str
    executions: tuple[AccountingExecution, ...]

    def canonical_value(self) -> dict[str, object]:
        return {
            "currency": self.currency,
            "executions": [execution.canonical_value() for execution in self.executions],
            "initial_cash": str(self.initial_cash),
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class AccountingEntryProvenance:
    simulated_fill_id: str
    simulated_fill_identity: ContentIdentity
    simulated_order_id: str
    fill_provenance_identity: ContentIdentity
    symbol: str
    side: SimulatedOrderSide
    quantity: Decimal
    fill_price: Decimal
    fill_time: datetime
    accounting_policy_id: AccountingPolicyId
    accounting_policy_version: str
    accounting_policy_identity: ContentIdentity
    previous_accounting_state_identity: ContentIdentity
    resulting_accounting_state_identity: ContentIdentity

    def canonical_value(self) -> dict[str, object]:
        return {
            "accounting_policy_id": str(self.accounting_policy_id),
            "accounting_policy_identity": str(self.accounting_policy_identity),
            "accounting_policy_version": self.accounting_policy_version,
            "fill_price": str(self.fill_price),
            "fill_provenance_identity": str(self.fill_provenance_identity),
            "fill_time": self.fill_time.isoformat(),
            "previous_accounting_state_identity": str(self.previous_accounting_state_identity),
            "quantity": str(self.quantity),
            "resulting_accounting_state_identity": str(self.resulting_accounting_state_identity),
            "side": self.side.value,
            "simulated_fill_id": self.simulated_fill_id,
            "simulated_fill_identity": str(self.simulated_fill_identity),
            "simulated_order_id": self.simulated_order_id,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class AccountingEntry:
    accounting_entry_id: AccountingEntryId
    cash_delta: Decimal
    realized_pnl_delta: Decimal
    provenance: AccountingEntryProvenance
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        cash_delta: Decimal,
        realized_pnl_delta: Decimal,
        provenance: AccountingEntryProvenance,
    ) -> AccountingEntry:
        value = {
            "cash_delta": str(cash_delta),
            "provenance": provenance.canonical_value(),
            "realized_pnl_delta": str(realized_pnl_delta),
        }
        identity = ContentIdentity.from_canonical(value)
        return cls(
            AccountingEntryId(f"accounting-entry:{identity}"),
            cash_delta,
            realized_pnl_delta,
            provenance,
            identity,
        )


@dataclass(frozen=True, slots=True)
class AccountingReplayResult:
    accounting_replay_id: AccountingReplayId
    status: AccountingStatus
    reason_code: AccountingReasonCode | None
    input_identity: ContentIdentity
    accounting_policy_identity: ContentIdentity
    initial_cash: Decimal
    final_state: AccountingState
    ledger: tuple[AccountingEntry, ...]
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        status: AccountingStatus,
        reason_code: AccountingReasonCode | None,
        input_identity: ContentIdentity,
        accounting_policy_identity: ContentIdentity,
        initial_cash: Decimal,
        final_state: AccountingState,
        ledger: tuple[AccountingEntry, ...],
    ) -> AccountingReplayResult:
        value = {
            "accounting_policy_identity": str(accounting_policy_identity),
            "final_state": final_state.canonical_value(),
            "initial_cash": str(initial_cash),
            "input_identity": str(input_identity),
            "ledger": [str(entry.content_identity) for entry in ledger],
            "reason_code": None if reason_code is None else reason_code.value,
            "status": status.value,
        }
        identity = ContentIdentity.from_canonical(value)
        return cls(
            AccountingReplayId(f"accounting-replay:{identity}"),
            status,
            reason_code,
            input_identity,
            accounting_policy_identity,
            initial_cash,
            final_state,
            ledger,
            identity,
        )


@dataclass(frozen=True, slots=True)
class AccountingMark:
    symbol: str
    price: Decimal
    event_time: datetime
    available_at: datetime
    source_data_identity: ContentIdentity
    source_snapshot_identity: ContentIdentity
    finality: DataFinality
    validation_as_of_use: DataQuality
    gap_status: GapStatus

    @classmethod
    def from_data(cls, *, point: DataPoint, snapshot: DatasetSnapshot) -> AccountingMark:
        import json

        if point not in snapshot.points:
            raise ValidationError("Accounting mark point must belong to its source snapshot")
        payload = json.loads(point.canonical_payload)
        return cls(
            symbol=point.symbol,
            price=Decimal(str(payload["close"])),
            event_time=point.temporal.event_time,
            available_at=point.temporal.available_at,
            source_data_identity=point.content_identity,
            source_snapshot_identity=snapshot.content_identity,
            finality=point.finality,
            validation_as_of_use=snapshot.validation_as_of_use,
            gap_status=snapshot.gap_status,
        )

    def canonical_value(self) -> dict[str, object]:
        return {
            "available_at": self.available_at.isoformat(),
            "event_time": self.event_time.isoformat(),
            "finality": self.finality.value,
            "gap_status": self.gap_status.value,
            "price": str(self.price),
            "source_data_identity": str(self.source_data_identity),
            "source_snapshot_identity": str(self.source_snapshot_identity),
            "symbol": self.symbol,
            "validation_as_of_use": self.validation_as_of_use.value,
        }

    @property
    def content_identity(self) -> ContentIdentity:
        return ContentIdentity.from_canonical(self.canonical_value())


@dataclass(frozen=True, slots=True)
class AccountingValuation:
    accounting_valuation_id: AccountingValuationId
    status: AccountingStatus
    reason_code: AccountingReasonCode | None
    cash: Decimal
    position: AccountingPosition
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None
    equity: Decimal | None
    mark_identity: ContentIdentity | None
    valuation_time: datetime
    accounting_state_identity: ContentIdentity
    accounting_policy_identity: ContentIdentity
    content_identity: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        status: AccountingStatus,
        reason_code: AccountingReasonCode | None,
        cash: Decimal,
        position: AccountingPosition,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal | None,
        equity: Decimal | None,
        mark_identity: ContentIdentity | None,
        valuation_time: datetime,
        accounting_state_identity: ContentIdentity,
        accounting_policy_identity: ContentIdentity,
    ) -> AccountingValuation:
        values: dict[str, object] = {
            "status": status.value,
            "reason_code": None if reason_code is None else reason_code.value,
            "cash": cash,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "equity": equity,
            "mark_identity": mark_identity,
            "valuation_time": valuation_time.isoformat(),
            "accounting_state_identity": accounting_state_identity,
            "accounting_policy_identity": accounting_policy_identity,
        }
        canonical = {
            key: (str(value) if isinstance(value, Decimal | ContentIdentity) else value)
            for key, value in values.items()
        }
        canonical["position"] = position.canonical_value()
        identity = ContentIdentity.from_canonical(canonical)
        return cls(
            accounting_valuation_id=AccountingValuationId(f"accounting-valuation:{identity}"),
            status=status,
            reason_code=reason_code,
            cash=cash,
            position=position,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            equity=equity,
            mark_identity=mark_identity,
            valuation_time=valuation_time,
            accounting_state_identity=accounting_state_identity,
            accounting_policy_identity=accounting_policy_identity,
            content_identity=identity,
        )
