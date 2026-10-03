"""Offline deterministic, cost-aware research backtesting."""
from .engine import run_backtest
from .enums import EvaluationResult, PositionSide, SignalAction
from .errors import (
    BacktestError, BacktestCompatibilityError, BacktestInputError, BacktestSignalConflictError,
)
from .evaluation import RuleEvaluator
from .models import (
    BacktestConfig, BacktestResult, ClosedTrade, CostBreakdown, EquityPoint,
    ExecutionCostConfig, Fill, Position, Signal,
)

__all__ = [
    "run_backtest", "EvaluationResult", "PositionSide", "SignalAction", "RuleEvaluator",
    "BacktestError", "BacktestCompatibilityError", "BacktestInputError", "BacktestSignalConflictError",
    "ExecutionCostConfig", "CostBreakdown",
    "BacktestConfig", "BacktestResult", "ClosedTrade", "EquityPoint", "Fill", "Position", "Signal",
]
