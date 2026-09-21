from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from atp.exchange.filters import NotionalPriceEvidence, parse_symbol_filters
from atp.exchange.read_only import EvidenceError
from atp.first_testnet_order.preparation import select_quantity
from atp.first_testnet_order.preparation_data import candle_snapshot
from atp.shared.environment import ACTIVE_ENVIRONMENTS, Environment
from atp.shared.errors import ConfigurationError, ValidationError
from atp.shared.identity import ContentIdentity
from atp.shared.time import LogicalTime
from atp.strategy import (
    SmaCrossoverConfig,
    SmaCrossoverStrategy,
    StrategyEvaluationContext,
    StrategyId,
)
from atp.testnet_activation.composition import validate_activation
from tests.activation_support import activation
from tests.contract.test_check_only_preparation import OfflineSource, candles
from tests.unit.test_release_deployment import inputs
from tests.unit.test_risk_engine import NOW

__all__ = ["activation", "inputs"]


def price(value="50000", kind="EXCHANGE_LAST_PRICE", window=0):
    return NotionalPriceEvidence(
        "BTCUSDT",
        Decimal(value),
        kind,
        ContentIdentity.from_text("exchange-test-price"),
        NOW,
        NOW,
        window,
    )


def symbol():
    return OfflineSource().responses["exchangeInfo"]["symbols"][0]


@pytest.mark.parametrize("value", ["50000", "60001.1234567890123456789", "0.00001", "1000000000"])
def test_quantity_exact_and_maximal(value):
    raw = symbol()
    filters = parse_symbol_filters(raw, NOW)
    if value == "1000000000":
        with pytest.raises(EvidenceError):
            select_quantity(filters, price(value), NOW)
        return
    a = select_quantity(filters, price(value), NOW)
    assert a == select_quantity(filters, price(value), NOW)
    assert a.selected_quantity % a.step_size == 0
    assert a.projected_quote_notional <= 6
    assert (
        a.selected_quantity + a.step_size
    ) * a.price > 6 or a.selected_quantity + a.step_size > a.max_quantity
    assert a.quote_cap == Decimal("6")


@pytest.mark.parametrize(
    "change", ["minimum", "notional", "unknown", "zero_step", "halt", "no_market", "no_grid"]
)
def test_impossible_filters_never_increase_cap(change):
    raw = symbol()
    if change == "minimum":
        raw["filters"][0]["minQty"] = "1"
    elif change == "notional":
        raw["filters"].append(
            dict(filterType="MIN_NOTIONAL", minNotional="7", applyToMarket=True, avgPriceMins=0)
        )
    elif change == "unknown":
        raw["filters"].append(dict(filterType="UNKNOWN"))
    elif change == "zero_step":
        raw["filters"][0]["stepSize"] = "0"
    elif change == "halt":
        raw["status"] = "HALT"
    elif change == "no_market":
        raw["orderTypes"] = ["LIMIT"]
    else:
        raw["filters"] = []
    with pytest.raises(EvidenceError):
        select_quantity(parse_symbol_filters(raw, NOW), price(), NOW)


def test_market_lot_and_notional_max_take_priority():
    raw = symbol()
    raw["filters"] += [
        dict(filterType="MARKET_LOT_SIZE", minQty="0.00001", maxQty="0.00009", stepSize="0.00003"),
        dict(
            filterType="NOTIONAL",
            minNotional="0",
            maxNotional="3",
            applyMinToMarket=False,
            applyMaxToMarket=True,
            avgPriceMins=0,
        ),
    ]
    selected = select_quantity(parse_symbol_filters(raw, NOW), price(), NOW)
    assert selected.selected_quantity == Decimal("0.00006")


@pytest.mark.parametrize("change", ["short", "gap", "current", "reordered", "duplicate", "float"])
def test_final_causal_51_candles_required(change):
    raw = deepcopy(candles())
    if change == "short":
        raw.pop()
    elif change == "gap":
        raw[20][0] -= 300000
        raw[20][6] -= 300000
    elif change == "current":
        for row in raw:
            row[0] += 300000
            row[6] += 300000
    elif change == "reordered":
        raw.reverse()
    elif change == "duplicate":
        raw[20] = raw[19]
    else:
        raw[-1][4] = 100.0
    with pytest.raises(EvidenceError):
        candle_snapshot(raw, NOW)


def test_strategy_testnet_context_is_conditional_and_bound(activation):
    snapshot, universe = candle_snapshot(candles(), NOW)
    original = ACTIVE_ENVIRONMENTS
    for ctx in (None, object()):
        with pytest.raises(ValidationError):
            StrategyEvaluationContext(
                Environment.TESTNET, snapshot, universe, LogicalTime(NOW), "BTCUSDT", ctx, "5m"
            )
    ctx = validate_activation(**activation).context
    context = StrategyEvaluationContext(
        Environment.TESTNET, snapshot, universe, LogicalTime(NOW), "BTCUSDT", ctx, "5m"
    )
    engine = SmaCrossoverStrategy(
        StrategyId("sma-crossover"), "1.0.0", SmaCrossoverConfig(short_window=20, long_window=50)
    )
    result = engine.evaluate(context)
    assert result.signal.kind.value == "LONG_ENTRY"
    assert result.provenance.runtime_authorization_identity == ctx.content_identity
    assert result.provenance.candle_interval == "5m"
    assert result.provenance.snapshot_content_identity == snapshot.content_identity
    with pytest.raises(ConfigurationError):
        replace(context, environment=Environment.LIVE)
    with pytest.raises(ValidationError):
        replace(context, evaluation_time=LogicalTime(NOW + timedelta(days=1)))
    object.__setattr__(context, "runtime_authorization", None)
    assert engine.evaluate(context).signal is None
    assert original == ACTIVE_ENVIRONMENTS
    assert Environment.TESTNET not in ACTIVE_ENVIRONMENTS
    with pytest.raises(TypeError):
        SmaCrossoverConfig()


def test_portfolio_source_is_bound_without_untrusted_stringification():
    from atp.first_testnet_order.preparation import portfolio_evidence, portfolio_for_risk
    from atp.risk import DeterministicRiskEngine, RiskEvaluationContext, RiskStatus
    from tests.unit.test_risk_engine import market_context, policy, strategy_evaluation

    account = OfflineSource().responses["account"]
    first = portfolio_for_risk(portfolio_evidence(account, [], NOW))
    second = portfolio_for_risk(portfolio_evidence(account, [], NOW + timedelta(seconds=1)))
    assert first.content_identity != second.content_identity

    class Untrusted:
        def __str__(self):
            pytest.fail("Untrusted source must not be stringified")

    object.__setattr__(first, "source_evidence_identity", Untrusted())
    result = DeterministicRiskEngine(policy()).evaluate(
        RiskEvaluationContext(strategy_evaluation(), market_context(), first)
    )
    assert result.status is RiskStatus.BLOCKED
