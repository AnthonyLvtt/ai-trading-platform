"""Kraken-only economic execution contracts.

This module defines deterministic order intent and durable state contracts only.
It does not grant runtime network or economic authority.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from atp.exchange.contracts import BTC_EUR, CanonicalInstrumentId, MarketKind, VenueId
from atp.risk.model import RiskDecision, RiskStatus
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.strategy.model import SignalKind, StrategyEvaluation, StrategySignal


class ExecutionError(ValueError):
    pass


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class SubmissionState(StrEnum):
    PREPARED = "PREPARED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    RECONCILED = "RECONCILED"


@dataclass(frozen=True, slots=True, init=False)
class OrderIntent:
    venue: VenueId
    instrument: CanonicalInstrumentId
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    strategy_evaluation_identity: ContentIdentity
    strategy_signal_identity: ContentIdentity
    risk_decision_identity: ContentIdentity
    idempotency_key: ContentIdentity

    @classmethod
    def create(
        cls,
        *,
        strategy_evaluation: StrategyEvaluation,
        risk_decision: RiskDecision,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        limit_price: Decimal | None = None,
        instrument: CanonicalInstrumentId = BTC_EUR,
    ) -> OrderIntent:
        _validate_binding(strategy_evaluation, risk_decision, side)
        _validate_order_fields(instrument, side, order_type, quantity, limit_price)
        signal = strategy_evaluation.signal
        assert signal is not None
        value = {
            "instrument": instrument.symbol,
            "limit_price": None if limit_price is None else str(limit_price),
            "order_type": order_type.value,
            "quantity": str(quantity),
            "risk_decision_identity": str(risk_decision.content_identity),
            "side": side.value,
            "strategy_evaluation_identity": str(strategy_evaluation.content_identity),
            "strategy_signal_identity": str(signal.content_identity),
            "venue": VenueId.KRAKEN.value,
        }
        intent = object.__new__(cls)
        object.__setattr__(intent, "venue", VenueId.KRAKEN)
        object.__setattr__(intent, "instrument", instrument)
        object.__setattr__(intent, "side", side)
        object.__setattr__(intent, "order_type", order_type)
        object.__setattr__(intent, "quantity", quantity)
        object.__setattr__(intent, "limit_price", limit_price)
        object.__setattr__(
            intent,
            "strategy_evaluation_identity",
            strategy_evaluation.content_identity,
        )
        object.__setattr__(intent, "strategy_signal_identity", signal.content_identity)
        object.__setattr__(intent, "risk_decision_identity", risk_decision.content_identity)
        object.__setattr__(
            intent,
            "idempotency_key",
            ContentIdentity.from_canonical(value),
        )
        intent.__post_init__()
        return intent

    def __post_init__(self) -> None:
        _validate_order_fields(
            self.instrument,
            self.side,
            self.order_type,
            self.quantity,
            self.limit_price,
        )
        identities = (
            self.strategy_evaluation_identity,
            self.strategy_signal_identity,
            self.risk_decision_identity,
            self.idempotency_key,
        )
        if self.venue is not VenueId.KRAKEN:
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        if any(type(value) is not ContentIdentity for value in identities):
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        expected = ContentIdentity.from_canonical(
            {
                "instrument": self.instrument.symbol,
                "limit_price": None if self.limit_price is None else str(self.limit_price),
                "order_type": self.order_type.value,
                "quantity": str(self.quantity),
                "risk_decision_identity": str(self.risk_decision_identity),
                "side": self.side.value,
                "strategy_evaluation_identity": str(self.strategy_evaluation_identity),
                "strategy_signal_identity": str(self.strategy_signal_identity),
                "venue": self.venue.value,
            }
        )
        if self.idempotency_key != expected:
            raise ExecutionError("EXECUTION_INTENT_INVALID")

    def validate(self) -> None:
        self.__post_init__()


def _validate_binding(
    strategy_evaluation: StrategyEvaluation,
    risk_decision: RiskDecision,
    side: OrderSide,
) -> None:
    if (
        type(strategy_evaluation) is not StrategyEvaluation
        or type(risk_decision) is not RiskDecision
    ):
        raise ExecutionError("RISK_BINDING_INVALID")
    try:
        strategy_evaluation.__post_init__()
        signal = strategy_evaluation.signal
        if type(signal) is not StrategySignal:
            raise ExecutionError("RISK_BINDING_INVALID")
        signal.__post_init__()
        risk_decision.__post_init__()
    except (AttributeError, TypeError, ValueError, ValidationError, RecursionError):
        raise ExecutionError("RISK_BINDING_INVALID") from None
    if risk_decision.status is not RiskStatus.APPROVED:
        raise ExecutionError("RISK_BINDING_INVALID")
    provenance = risk_decision.provenance
    if provenance.strategy_evaluation_identity != strategy_evaluation.content_identity:
        raise ExecutionError("RISK_BINDING_INVALID")
    if provenance.strategy_signal_identity != signal.content_identity:
        raise ExecutionError("RISK_BINDING_INVALID")
    expected = SignalKind.LONG_ENTRY if side is OrderSide.BUY else SignalKind.EXIT
    if signal.kind is not expected:
        raise ExecutionError("STRATEGY_SIDE_MISMATCH")
    if strategy_evaluation.provenance.symbol != BTC_EUR.symbol:
        raise ExecutionError("INSTRUMENT_NOT_AUTHORIZED")


def _validate_order_fields(
    instrument: CanonicalInstrumentId,
    side: OrderSide,
    order_type: OrderType,
    quantity: Decimal,
    limit_price: Decimal | None,
) -> None:
    instrument_valid = (
        type(instrument) is CanonicalInstrumentId
        and instrument == BTC_EUR
        and instrument.market is MarketKind.SPOT
    )
    quantity_valid = type(quantity) is Decimal and quantity.is_finite() and quantity > Decimal(0)
    if not instrument_valid:
        raise ExecutionError("ORDER_FIELDS_INVALID")
    if type(side) is not OrderSide or type(order_type) is not OrderType:
        raise ExecutionError("ORDER_FIELDS_INVALID")
    if not quantity_valid:
        raise ExecutionError("ORDER_FIELDS_INVALID")
    if order_type is OrderType.MARKET and limit_price is not None:
        raise ExecutionError("MARKET_PRICE_FORBIDDEN")
    if order_type is OrderType.LIMIT:
        if type(limit_price) is not Decimal:
            raise ExecutionError("LIMIT_PRICE_REQUIRED")
        if not limit_price.is_finite() or limit_price <= Decimal(0):
            raise ExecutionError("LIMIT_PRICE_REQUIRED")


_ALLOWED_TRANSITIONS: dict[SubmissionState, frozenset[SubmissionState]] = {
    SubmissionState.PREPARED: frozenset({SubmissionState.ATTEMPT_STARTED}),
    SubmissionState.ATTEMPT_STARTED: frozenset(
        {SubmissionState.ACKNOWLEDGED, SubmissionState.UNKNOWN}
    ),
    SubmissionState.ACKNOWLEDGED: frozenset({SubmissionState.RECONCILED}),
    SubmissionState.UNKNOWN: frozenset({SubmissionState.RECONCILED}),
    SubmissionState.RECONCILED: frozenset(),
}


class ExecutionLedger:
    """SQLite-backed append-only transition ledger.

    The ledger never performs network calls and cannot authorize submission.
    """

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise ExecutionError("LEDGER_PATH_INVALID")
        if path.exists() and path.is_symlink():
            raise ExecutionError("LEDGER_PATH_INVALID")
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_transitions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    previous_state TEXT,
                    reason_code TEXT NOT NULL,
                    UNIQUE(idempotency_key, state)
                )
                """
            )
            db.commit()

    def prepare(self, intent: OrderIntent) -> None:
        if type(intent) is not OrderIntent:
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        intent.validate()
        self._append(
            intent.idempotency_key,
            SubmissionState.PREPARED,
            "INTENT_VALIDATED",
        )

    def transition(
        self,
        intent: OrderIntent,
        target: SubmissionState,
        *,
        reason_code: str,
    ) -> None:
        if type(intent) is not OrderIntent:
            raise ExecutionError("EXECUTION_INTENT_INVALID")
        intent.validate()
        if type(target) is not SubmissionState:
            raise ExecutionError("LEDGER_INPUT_INVALID")
        if type(reason_code) is not str or not re.fullmatch(r"[A-Z0-9_]{3,80}", reason_code):
            raise ExecutionError("LEDGER_REASON_INVALID")
        self._append(intent.idempotency_key, target, reason_code)

    def current_state(self, key: ContentIdentity) -> SubmissionState | None:
        if type(key) is not ContentIdentity:
            raise ExecutionError("LEDGER_KEY_INVALID")
        self.initialize()
        with sqlite3.connect(self._path) as db:
            return self._read_validated_state(db, key)

    @staticmethod
    def _read_validated_state(
        db: sqlite3.Connection, key: ContentIdentity
    ) -> SubmissionState | None:
        rows = db.execute(
            "SELECT state, previous_state, reason_code FROM execution_transitions "
            "WHERE idempotency_key = ? ORDER BY sequence",
            (str(key),),
        ).fetchall()
        current: SubmissionState | None = None
        for state_text, previous_text, reason_code in rows:
            try:
                state = SubmissionState(state_text)
            except ValueError:
                raise ExecutionError("LEDGER_HISTORY_INVALID") from None
            if (
                (current is None and state is not SubmissionState.PREPARED)
                or (current is not None and state not in _ALLOWED_TRANSITIONS[current])
                or previous_text != (None if current is None else current.value)
                or type(reason_code) is not str
                or not re.fullmatch(r"[A-Z0-9_]{3,80}", reason_code)
            ):
                raise ExecutionError("LEDGER_HISTORY_INVALID")
            current = state
        return current

    def _append(
        self,
        key: ContentIdentity,
        state: SubmissionState,
        reason_code: str,
    ) -> None:
        if (
            type(key) is not ContentIdentity
            or type(state) is not SubmissionState
            or type(reason_code) is not str
            or not re.fullmatch(r"[A-Z0-9_]{3,80}", reason_code)
        ):
            raise ExecutionError("LEDGER_INPUT_INVALID")
        self.initialize()
        query = (
            "INSERT INTO execution_transitions "
            "(idempotency_key, state, previous_state, reason_code) "
            "VALUES (?, ?, ?, ?)"
        )
        try:
            with sqlite3.connect(self._path) as db:
                # A write lock covers both the state decision and its append.
                db.execute("BEGIN IMMEDIATE")
                previous = self._read_validated_state(db, key)
                if state is SubmissionState.PREPARED:
                    if previous is not None:
                        raise ExecutionError("IDEMPOTENCY_KEY_ALREADY_RECORDED")
                elif previous is None or state not in _ALLOWED_TRANSITIONS[previous]:
                    raise ExecutionError("LEDGER_TRANSITION_INVALID")
                db.execute(
                    query,
                    (
                        str(key),
                        state.value,
                        None if previous is None else previous.value,
                        reason_code,
                    ),
                )
                db.commit()
        except sqlite3.IntegrityError:
            raise ExecutionError("IDEMPOTENCY_KEY_ALREADY_RECORDED") from None


class EconomicExecutionPort:
    """Structural non-authority boundary.

    A concrete network implementation is intentionally absent from ENG-EXCH-KRAKEN-007.
    """

    def submit(self, intent: OrderIntent) -> None:
        raise ExecutionError("KRAKEN_ECONOMIC_EXECUTION_NOT_QUALIFIED")
