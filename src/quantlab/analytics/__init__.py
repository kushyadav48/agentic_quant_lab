"""Offline deterministic performance analytics over BacktestResult."""
from .drawdown import compute_drawdown_statistics
from .errors import AnalyticsError, AnalyticsInputError
from .models import (
    AnalyticsConfig, DrawdownEpisode, DrawdownPoint, DrawdownStatistics,
    PerformanceReport, ReturnPoint, ReturnStatistics, TradeStatistics,
)
from .performance import analyze_performance
from .returns import compute_return_statistics

__all__ = [
    "analyze_performance", "compute_return_statistics", "compute_drawdown_statistics",
    "AnalyticsError", "AnalyticsInputError", "AnalyticsConfig", "PerformanceReport",
    "TradeStatistics", "ReturnPoint", "ReturnStatistics", "DrawdownPoint",
    "DrawdownEpisode", "DrawdownStatistics",
]
