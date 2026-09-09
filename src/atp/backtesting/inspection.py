"""Public causal-time inspection of an existing replay, never execution."""

from datetime import datetime

from atp.backtesting.engine import BacktestInput, _replay_input_structure_valid
from atp.backtesting.model import BacktestResult, ReplayStepResult, SimulatedFill, SimulatedOrder
from atp.backtesting.policy import SimulationPolicy
from atp.shared.errors import ValidationError
from atp.shared.time import require_utc


def backtest_causal_time(result: object, replay_input: object) -> datetime | None:
    """Latest processed evaluation/order/fill time; None means no verified causal time."""
    if type(result) is not BacktestResult or not _replay_input_structure_valid(replay_input):
        return None
    assert isinstance(result, BacktestResult) and isinstance(replay_input, BacktestInput)
    try:
        if (
            result.input_identity != replay_input.content_identity
            or result.simulation_policy_identity != SimulationPolicy.v1().content_identity
            or type(result.steps) is not tuple
            or len(result.steps) > len(replay_input.steps)
        ):
            return None
        times = [
            step.strategy_evaluation.provenance.evaluation_time.value
            for step in replay_input.steps[: len(result.steps)]
        ]
        for step in result.steps:
            if type(step) is not ReplayStepResult:
                return None
            step.__post_init__()
            if step.order is not None:
                if type(step.order) is not SimulatedOrder:
                    return None
                step.order.__post_init__()
                times.append(step.order.created_at)
            if step.fill is not None:
                if type(step.fill) is not SimulatedFill:
                    return None
                step.fill.__post_init__()
                times.append(step.fill.fill_time)
        result.__post_init__()
        for time in times:
            if type(time) is not datetime:
                return None
            require_utc(time)
        return max(times) if times else None
    except (AttributeError, TypeError, ValueError, ArithmeticError, ValidationError):
        return None
