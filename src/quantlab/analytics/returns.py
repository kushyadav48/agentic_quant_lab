"""Equity-relative returns and unannualized population statistics."""
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext

from quantlab.backtesting import EquityPoint
from ._validation import validate_equity
from .errors import AnalyticsInputError
from .models import AnalyticsConfig, ReturnPoint, ReturnStatistics


def compute_return_statistics(initial_capital: Decimal, curve: tuple[EquityPoint, ...],
                              config: AnalyticsConfig | None = None) -> ReturnStatistics:
    """Include the first capital-to-mark return; never discard undefined periods.

    A nonpositive previous equity makes that period undefined. If any period is
    undefined, mean/volatility/Sharpe for the complete series are None. The total
    capital-relative return remains defined, even for negative ending equity.
    """
    try:
        with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
            config = AnalyticsConfig() if config is None else AnalyticsConfig.model_validate(config)
            points = validate_equity(initial_capital, curve)
            previous = initial_capital
            series = []
            for point in points:
                value = point.equity / previous - 1 if previous > 0 else None
                series.append(ReturnPoint(timestamp=point.timestamp, period_return=value))
                previous = point.equity
            undefined = sum(point.period_return is None for point in series)
            mean = volatility = sharpe = annualized = None
            if series and not undefined:
                values = tuple(point.period_return for point in series)
                mean = sum(values, Decimal(0)) / len(values)
                variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / len(values)
                volatility = variance.sqrt()
                excess = tuple(value - config.risk_free_rate_per_period for value in values)
                mean_excess = sum(excess, Decimal(0)) / len(excess)
                excess_variance = sum(((value - mean_excess) ** 2 for value in excess), Decimal(0)) / len(excess)
                excess_volatility = excess_variance.sqrt()
                if excess_volatility > 0:
                    sharpe = mean_excess / excess_volatility
                    if config.annualization_factor is not None:
                        annualized = sharpe * config.annualization_factor.sqrt()
            return ReturnStatistics(absolute_return=previous - initial_capital,
                cumulative_return=previous / initial_capital - 1, series=tuple(series),
                undefined_period_count=undefined, mean_period_return=mean,
                return_volatility=volatility, period_sharpe=sharpe, annualized_sharpe=annualized)
    except (ValueError, TypeError, DecimalException) as exc:
        raise AnalyticsInputError(f"invalid return input or calculation at precision 34: {exc}") from exc
