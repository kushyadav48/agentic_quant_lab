"""Schema and equity consistency checks, without repairing or replaying input."""
from decimal import Decimal

from pydantic import TypeAdapter
from quantlab.backtesting import BacktestResult, EquityPoint
from quantlab.data.models import PositiveDecimal
from .errors import AnalyticsInputError


def validate_equity(initial_capital: Decimal, curve: tuple[EquityPoint, ...]
                    ) -> tuple[EquityPoint, ...]:
    TypeAdapter(PositiveDecimal).validate_python(initial_capital, strict=True)
    if type(curve) is not tuple or any(type(point) is not EquityPoint for point in curve):
        raise AnalyticsInputError("equity curve requires a tuple of canonical EquityPoint objects")
    points = TypeAdapter(tuple[EquityPoint, ...]).validate_python(curve, strict=True)
    previous = None
    for point in points:
        if previous is not None and point.timestamp <= previous:
            raise AnalyticsInputError("equity timestamps must be strictly increasing")
        if point.equity != initial_capital + point.realized_pnl + point.unrealized_pnl:
            raise AnalyticsInputError("equity point must equal capital + realized + unrealized P&L")
        previous = point.timestamp
    return points


def validate_result(result: BacktestResult) -> BacktestResult:
    if type(result) is not BacktestResult:
        raise AnalyticsInputError("analytics requires a canonical BacktestResult")
    result = BacktestResult.model_validate(result)
    curve = validate_equity(result.initial_capital, result.equity_curve)
    if not curve:
        raise AnalyticsInputError("BacktestResult requires a nonempty equity curve")
    if result.final_equity != result.initial_capital + result.realized_pnl + result.unrealized_pnl:
        raise AnalyticsInputError("final equity must equal capital + realized + unrealized P&L")
    last = curve[-1]
    if (last.equity, last.realized_pnl, last.unrealized_pnl) != (
            result.final_equity, result.realized_pnl, result.unrealized_pnl):
        raise AnalyticsInputError("ending account values must match the final equity point")
    if result.open_position is None and result.unrealized_pnl != 0:
        raise AnalyticsInputError("flat final account cannot have unrealized P&L")
    previous_exit = None
    for trade in result.closed_trades:
        if trade.quantity != result.quantity:
            raise AnalyticsInputError("closed trade quantity must match backtest quantity")
        if (previous_exit is not None and trade.entry_time <= previous_exit
                or trade.exit_time > last.timestamp):
            raise AnalyticsInputError("closed trade times must be ordered, disjoint and within the curve")
        previous_exit = trade.exit_time
    position = result.open_position
    if position is not None:
        if position.quantity != result.quantity:
            raise AnalyticsInputError("open position quantity must match backtest quantity")
        if (position.entry_time > last.timestamp
                or previous_exit is not None and position.entry_time <= previous_exit):
            raise AnalyticsInputError("open position time must follow closed trades and precede curve end")
    return result
