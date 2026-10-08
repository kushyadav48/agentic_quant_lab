"""Pure observed-side pricing, sharing historical arithmetic and Fill validation."""
from decimal import Decimal, DecimalException

from pydantic import ValidationError

from quantlab.backtesting import ExecutionCostConfig, Fill, SignalAction
from quantlab.backtesting.execution import price_execution
from .errors import PaperPricingError
from .models import MarketDelivery, OrderSide, OrderSubmission


def price_quote(submission: OrderSubmission, source: MarketDelivery,
                costs: ExecutionCostConfig) -> Fill:
    """No risk permission or lifecycle authority is granted by this calculation."""
    try:
        submission = OrderSubmission.model_validate(submission)
        source = MarketDelivery.model_validate(source)
        costs = ExecutionCostConfig.model_validate(costs)
        if costs.spread != 0:
            raise ValueError("synthetic spread is unsupported with observed quotes")
        if (source.quote.instrument_id != submission.instrument_id
                or source.sequence <= submission.sequence
                or source.quote.timestamp < submission.timestamp
                or source.timestamp < submission.timestamp):
            raise ValueError("pricing requires a subsequent eligible quote")
        buy = submission.side is OrderSide.BUY
        reference = source.quote.ask if buy else source.quote.bid
        price, breakdown = price_execution(reference, submission.quantity, costs,
            buy=buy, spread_adjustment=Decimal("0"))
        return Fill(action=SignalAction.ENTER_LONG if buy else SignalAction.ENTER_SHORT,
            signal_time=submission.timestamp, execution_time=source.timestamp,
            reference_price=reference, execution_price=price, quantity=submission.quantity,
            slippage_adjustment=costs.slippage, costs=breakdown)
    except (ValueError, TypeError, DecimalException, ValidationError) as exc:
        raise PaperPricingError("quote execution cannot be represented or reconciled") from exc
