from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from atp.data.consumption import ConsumerContract, build_historical_view
from atp.data.snapshot import (
    DataFinality,
    DataPoint,
    DataQuality,
    FreshnessStatus,
    GapStatus,
)
from atp.shared.errors import DomainError, ValidationError
from atp.strategy.identity import StrategyId
from atp.strategy.model import (
    ReasonCode,
    SignalKind,
    SignalProvenance,
    SmaCrossoverConfig,
    StrategyEvaluation,
    StrategyEvaluationContext,
    UsedDataPoint,
)

_BASELINE_DATA_CONTRACT = ConsumerContract(
    accepted_quality=frozenset({DataQuality.VALID}),
    accepted_freshness=frozenset({FreshnessStatus.FRESH}),
    accepted_finality=frozenset({DataFinality.FINAL}),
    accepted_gap_statuses=frozenset({GapStatus.NO_GAP_DETECTED}),
)


@dataclass(frozen=True, slots=True)
class SmaCrossoverStrategy:
    strategy_id: StrategyId
    version: str
    configuration: SmaCrossoverConfig

    def __post_init__(self) -> None:
        if not self.version or self.version.strip() != self.version:
            raise ValidationError("Strategy version must be non-empty and trimmed")

    def evaluate(self, context: StrategyEvaluationContext) -> StrategyEvaluation:
        empty_provenance = self._provenance(context, ())
        if context.snapshot.environment is not context.environment:
            return StrategyEvaluation.blocked(empty_provenance, ReasonCode.SNAPSHOT_INCOMPATIBLE)
        if (
            context.universe.effective_at > context.evaluation_time.value
            or context.snapshot.snapshot_id not in context.universe.source_snapshot_ids
            or context.symbol not in context.universe.eligible_symbols
        ):
            return StrategyEvaluation.blocked(empty_provenance, ReasonCode.UNIVERSE_INCOMPATIBLE)
        try:
            view = build_historical_view(
                snapshot=context.snapshot,
                universe=context.universe,
                as_of=context.evaluation_time.value,
                contract=_BASELINE_DATA_CONTRACT,
            )
        except DomainError:
            return StrategyEvaluation.blocked(
                empty_provenance, ReasonCode.DATA_CONTRACT_UNSATISFIED
            )

        symbol_points = tuple(point for point in view.points if point.symbol == context.symbol)
        minimum_history = self.configuration.long_window + 1
        if len(symbol_points) < minimum_history:
            return StrategyEvaluation.blocked(
                self._provenance(context, symbol_points), ReasonCode.INSUFFICIENT_HISTORY
            )

        used_points = symbol_points[-minimum_history:]
        try:
            closes = tuple(_close(point) for point in used_points)
        except ValueError:
            return StrategyEvaluation.blocked(
                self._provenance(context, used_points), ReasonCode.INVALID_CLOSE
            )

        previous_short = _sma(closes[:-1], self.configuration.short_window)
        previous_long = _sma(closes[:-1], self.configuration.long_window)
        current_short = _sma(closes, self.configuration.short_window)
        current_long = _sma(closes, self.configuration.long_window)

        if previous_short <= previous_long and current_short > current_long:
            signal_kind = SignalKind.LONG_ENTRY
        elif previous_short >= previous_long and current_short < current_long:
            signal_kind = SignalKind.EXIT
        else:
            signal_kind = SignalKind.NO_ACTION
        return StrategyEvaluation.completed(self._provenance(context, used_points), signal_kind)

    def _provenance(
        self, context: StrategyEvaluationContext, points: tuple[DataPoint, ...]
    ) -> SignalProvenance:
        return SignalProvenance(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            configuration=self.configuration,
            environment=context.environment,
            dataset_id=context.snapshot.dataset_id,
            snapshot_id=context.snapshot.snapshot_id,
            snapshot_content_identity=context.snapshot.content_identity,
            universe_snapshot_id=context.universe.universe_snapshot_id,
            universe_content_identity=context.universe.content_identity,
            evaluation_time=context.evaluation_time,
            symbol=context.symbol,
            used_data=tuple(
                UsedDataPoint(
                    content_identity=point.content_identity,
                    event_time=point.temporal.event_time,
                    available_at=point.temporal.available_at,
                )
                for point in points
            ),
        )


def _close(point: DataPoint) -> Decimal:
    payload = cast(object, json.loads(point.canonical_payload))
    if not isinstance(payload, dict) or "close" not in payload:
        raise ValueError("candle close is required")
    raw = payload["close"]
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise ValueError("candle close must be numeric")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("candle close must be numeric") from exc
    if not value.is_finite():
        raise ValueError("candle close must be finite")
    return value


def _sma(values: tuple[Decimal, ...], window: int) -> Decimal:
    selected = values[-window:]
    return sum(selected, start=Decimal(0)) / Decimal(window)
