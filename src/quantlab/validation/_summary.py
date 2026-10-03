"""Decimal descriptions of independent runs, never portfolio compounding."""
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from quantlab.analytics import PerformanceReport
from .models import DescriptiveSummary


def summarize(reports: tuple[PerformanceReport, ...]) -> DescriptiveSummary:
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        values = tuple(r.returns.cumulative_return for r in reports)
        ordered = sorted(values)
        n = len(values)
        middle = n // 2
        median = ordered[middle] if n % 2 else (ordered[middle-1] + ordered[middle]) / 2
        drawdowns = tuple(r.drawdown.maximum_drawdown_percentage for r in reports)
        return DescriptiveSummary(evaluation_count=n,
            profitable_count=sum(v > 0 for v in values), losing_count=sum(v < 0 for v in values),
            breakeven_count=sum(v == 0 for v in values),
            mean_total_return=sum(values, Decimal(0))/n, median_total_return=median,
            minimum_total_return=min(values), maximum_total_return=max(values),
            total_return_range=max(values)-min(values),
            closed_trade_count=sum(r.trades.closed_trade_count for r in reports),
            mean_maximum_drawdown=sum(drawdowns, Decimal(0))/n,
            minimum_maximum_drawdown=min(drawdowns), maximum_maximum_drawdown=max(drawdowns))
