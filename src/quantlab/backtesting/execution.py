"""Next-open deterministic pricing only; no rules, future OHLC or accounting."""
from decimal import Decimal, DecimalException, localcontext

from pydantic import ValidationError
from quantlab._decimal import deterministic_context
from quantlab.data import MarketBar, PriceType
from .enums import SignalAction
from .errors import BacktestCompatibilityError, BacktestInputError
from .models import CostBreakdown, ExecutionCostConfig, Fill, Signal


def validate_execution_compatibility(price_type: PriceType, costs: ExecutionCostConfig) -> None:
    if price_type is PriceType.TRADE and costs.spread != 0:
        raise BacktestCompatibilityError("TRADE bars cannot support nonzero synthetic spread")


def _next_open_fill(signal: Signal, bar: MarketBar, quantity: Decimal,
                    costs: ExecutionCostConfig) -> Fill:
    """Use only open/start/price basis; complete-bar publication is irrelevant."""
    validate_execution_compatibility(bar.price_type, costs)
    buy = signal.action in (SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT)
    try:
        with localcontext(deterministic_context()):
            if bar.price_type is PriceType.MID:
                spread = costs.spread / Decimal("2")
            elif (bar.price_type is PriceType.BID and buy
                  or bar.price_type is PriceType.ASK and not buy):
                spread = costs.spread
            else:
                spread = Decimal("0")
            # Preserve the exact Phase 7 open, including excess precision, at zero costs.
            price = bar.open
            if spread:
                price = price + spread if buy else price - spread
            if costs.slippage:
                price = price + costs.slippage if buy else price - costs.slippage
            if price <= 0:
                raise BacktestInputError("execution costs produce a nonpositive execution price")
            return Fill(action=signal.action, signal_time=signal.signal_time,
                execution_time=bar.start_time, reference_price=bar.open,
                execution_price=price, quantity=quantity, spread_adjustment=spread,
                slippage_adjustment=costs.slippage,
                costs=CostBreakdown(spread_cost=spread * quantity,
                    slippage_cost=costs.slippage * quantity,
                    commission=costs.commission_per_unit * quantity,
                    fees=costs.fixed_fee_per_fill))
    except (DecimalException, ValidationError) as exc:
        raise BacktestInputError(f"execution costs cannot be represented at precision 34: {exc}") from exc
