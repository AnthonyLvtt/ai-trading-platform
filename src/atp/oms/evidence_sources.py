"""Offline OMS producers for spendable-EUR and bounded-fee evidence.

These functions consume already-supplied payloads. They contain no credentials,
signing, nonce generation, network transport, or runtime source authority.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, DecimalException, Inexact, localcontext

from atp.exchange.contracts import BTC_EUR, VenueId
from atp.exchange.execution import OrderIntent, OrderSide, OrderType
from atp.exchange.kraken.extended_balance import parse_offline_extended_balance
from atp.exchange.kraken.trade_volume import parse_offline_trade_volume_fee_bound
from atp.oms.exposure import BoundedFeeEvidence, ExposureError, SpendableEurEvidence
from atp.oms.exposure_qualification import BOUNDED_FEE_SOURCE, SPENDABLE_EUR_SOURCE
from atp.shared.identity import ContentIdentity


def _aware(value: datetime) -> bool:
    return type(value) is datetime and value.tzinfo is not None


def spendable_eur_from_offline_balance_ex(
    *,
    payload: object,
    account_identity: ContentIdentity,
    credential_reference_identity: ContentIdentity,
    observed_at: datetime,
) -> SpendableEurEvidence:
    if (
        type(account_identity) is not ContentIdentity
        or type(credential_reference_identity) is not ContentIdentity
        or not _aware(observed_at)
    ):
        raise ExposureError("SPENDABLE_EUR_SOURCE_INPUT_INVALID")
    rows = parse_offline_extended_balance(payload)
    eur = tuple(row for row in rows if row.asset == "EUR")
    if len(eur) != 1:
        raise ExposureError("SPENDABLE_EUR_SOURCE_EUR_REQUIRED")
    return SpendableEurEvidence(
        venue=VenueId.KRAKEN,
        account_identity=account_identity,
        credential_reference_identity=credential_reference_identity,
        balance_source_identity=ContentIdentity.from_canonical(payload),
        amount_eur=eur[0].available,
        observed_at=observed_at,
        qualification_identity=SPENDABLE_EUR_SOURCE.content_identity,
    )


def bounded_fee_from_offline_trade_volume(
    *,
    payload: object,
    account_identity: ContentIdentity,
    intent: OrderIntent,
    observed_at: datetime,
) -> BoundedFeeEvidence:
    if (
        type(account_identity) is not ContentIdentity
        or type(intent) is not OrderIntent
        or not _aware(observed_at)
    ):
        raise ExposureError("BOUNDED_FEE_SOURCE_INPUT_INVALID")
    intent.validate()
    if (
        intent.venue is not VenueId.KRAKEN
        or intent.instrument != BTC_EUR
        or intent.side is not OrderSide.BUY
        or intent.order_type is not OrderType.LIMIT
        or intent.limit_price is None
    ):
        raise ExposureError("BOUNDED_FEE_SOURCE_CANDIDATE_INVALID")
    bound = parse_offline_trade_volume_fee_bound(payload)
    try:
        with localcontext() as context:
            context.prec = 100
            context.traps[Inexact] = True
            notional = intent.quantity * intent.limit_price
            maximum_fee_eur = notional * bound.taker_max_fee_percent / Decimal("100")
    except DecimalException:
        raise ExposureError("BOUNDED_FEE_SOURCE_ARITHMETIC_UNSAFE") from None
    return BoundedFeeEvidence(
        venue=VenueId.KRAKEN,
        account_identity=account_identity,
        candidate_identity=intent.idempotency_key,
        maximum_fee_eur=maximum_fee_eur,
        observed_at=observed_at,
        source_identity=ContentIdentity.from_canonical(payload),
        qualification_identity=BOUNDED_FEE_SOURCE.content_identity,
    )
