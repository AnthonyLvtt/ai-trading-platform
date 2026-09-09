"""Read-only public verification of accounting artefacts."""

from dataclasses import fields
from datetime import datetime
from decimal import Decimal

from atp.accounting.engine import _valid_replay_result
from atp.accounting.model import (
    AccountingPosition,
    AccountingPositionStatus,
    AccountingReasonCode,
    AccountingReplayResult,
    AccountingStatus,
    AccountingValuation,
)
from atp.accounting.policy import ACCOUNTING_POLICY_V1
from atp.shared.errors import ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import require_utc


def validate_accounting_replay(value: object) -> bool:
    """Validate the existing canonical replay/ledger contract, without applying fills."""
    if type(value) is not AccountingReplayResult:
        return False
    try:
        return _valid_replay_result(value, ACCOUNTING_POLICY_V1)
    except (AttributeError, TypeError, ValueError, ArithmeticError, ValidationError):
        return False


def validate_accounting_valuation(value: object) -> bool:
    """Reconstruct canonical identity and derived ID without executing valuation."""
    if type(value) is not AccountingValuation:
        return False
    assert isinstance(value, AccountingValuation)
    try:
        if (
            type(value.status) is not AccountingStatus
            or type(value.reason_code) not in (AccountingReasonCode, type(None))
            or type(value.valuation_time) is not datetime
            or type(value.position) is not AccountingPosition
            or type(value.position.status) is not AccountingPositionStatus
        ):
            return False
        if (value.status is AccountingStatus.COMPLETED) != (value.reason_code is None):
            return False
        for amount in (value.cash, value.realized_pnl):
            if type(amount) is not Decimal or not amount.is_finite():
                return False
        for optional_amount in (value.unrealized_pnl, value.equity):
            if optional_amount is not None and (
                type(optional_amount) is not Decimal or not optional_amount.is_finite()
            ):
                return False
        if value.cash < 0 or (
            value.status is AccountingStatus.COMPLETED
            and (value.equity is None or value.unrealized_pnl is None)
        ):
            return False
        for i in (
            value.content_identity,
            value.accounting_state_identity,
            value.accounting_policy_identity,
            value.mark_identity,
        ):
            if i is None and i is value.mark_identity:
                continue
            if type(i) is not ContentIdentity:
                return False
            assert isinstance(i, ContentIdentity)
            if type(i.algorithm) is not str or type(i.digest) is not str:
                return False
            i.__post_init__()
        if value.accounting_policy_identity != ACCOUNTING_POLICY_V1.content_identity:
            return False
        value.position.__post_init__()
        require_utc(value.valuation_time)
        rebuilt = AccountingValuation.create(
            **{
                f.name: getattr(value, f.name)
                for f in fields(value)
                if f.name not in ("content_identity", "accounting_valuation_id")
            }
        )
        return rebuilt == value
    except (AttributeError, TypeError, ValueError, ArithmeticError, ValidationError):
        return False
