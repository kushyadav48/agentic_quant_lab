"""Auditable analytics over completed output; no execution or signal decisions."""
from datetime import timedelta
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext

from quantlab.backtesting import BacktestResult, ClosedTrade
from ._validation import validate_result
from .drawdown import compute_drawdown_statistics
from .errors import AnalyticsInputError
from .models import AnalyticsConfig, PerformanceReport, TradeStatistics
from .returns import compute_return_statistics


def _microseconds(duration: timedelta) -> int:
    return (duration.days * 86400 + duration.seconds) * 1000000 + duration.microseconds


def _trade_statistics(trades: tuple[ClosedTrade, ...]) -> TradeStatistics:
    values = tuple(t.net_pnl for t in trades)
    winners = tuple(value for value in values if value > 0)
    losers = tuple(value for value in values if value < 0)
    count = len(values)
    profit = sum(winners, Decimal(0))
    loss = -sum(losers, Decimal(0))
    net = sum(values, Decimal(0))
    durations = tuple(t.exit_time - t.entry_time for t in trades)
    # timedelta supports whole microseconds. Round its exact integer mean to the
    # nearest microsecond, ties to even, without total_seconds() binary floats.
    average_duration = None
    if durations:
        mean_us = Decimal(sum(_microseconds(d) for d in durations)) / count
        average_duration = timedelta(microseconds=int(mean_us.to_integral_value(rounding=ROUND_HALF_EVEN)))
    return TradeStatistics(closed_trade_count=count, winning_trade_count=len(winners),
        losing_trade_count=len(losers), breakeven_trade_count=count - len(winners) - len(losers),
        win_rate=Decimal(len(winners)) / count if count else None,
        loss_rate=Decimal(len(losers)) / count if count else None,
        gross_profit=profit, gross_loss=loss, net_closed_trade_pnl=net,
        average_trade=net / count if count else None,
        average_winner=profit / len(winners) if winners else None,
        average_loser=-loss / len(losers) if losers else None,
        largest_winner=max(winners) if winners else None,
        largest_loser=min(losers) if losers else None,
        profit_factor=profit / loss if loss > 0 else None,
        average_holding_duration=average_duration,
        minimum_holding_duration=min(durations) if durations else None,
        maximum_holding_duration=max(durations) if durations else None)


def analyze_performance(result: BacktestResult,
                        config: AnalyticsConfig | None = None) -> PerformanceReport:
    """Revalidate and measure output without altering simulation accounting.

    Closed-trade metrics use net_pnl. Account return uses final equity, including
    open-position marks and already-paid entry explicit costs. Aggregating rounded
    trade net P&Ls is not required to equal the engine's event-ordered realized
    accumulator: Decimal addition/subtraction is nonassociative at precision 34.
    """
    try:
        with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
            result = validate_result(result)
            config = AnalyticsConfig() if config is None else AnalyticsConfig.model_validate(config)
            return PerformanceReport(strategy_id=result.strategy_id,
                strategy_version=result.strategy_version,
                strategy_content_digest=result.strategy_content_digest,
                instrument_id=result.instrument_id, timeframe=result.timeframe,
                price_type=result.price_type, config=config,
                initial_capital=result.initial_capital, final_equity=result.final_equity,
                realized_pnl=result.realized_pnl, unrealized_pnl=result.unrealized_pnl,
                has_open_position=result.open_position is not None,
                trades=_trade_statistics(result.closed_trades),
                returns=compute_return_statistics(result.initial_capital, result.equity_curve, config),
                drawdown=compute_drawdown_statistics(result.initial_capital, result.equity_curve))
    except (ValueError, TypeError, DecimalException) as exc:
        raise AnalyticsInputError(f"invalid analytics input or calculation at precision 34: {exc}") from exc
