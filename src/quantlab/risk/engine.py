"""Pure hard-limit evaluation; no strategy, execution, analytics or mutable state."""
from decimal import Decimal
from fractions import Fraction

from .errors import RiskInputError
from .models import RiskAction, RiskConfig, RiskContext, RiskDecision, RiskReason


def evaluate_entry_risk(context: RiskContext, config: RiskConfig) -> RiskDecision:
    """Revalidate inputs and report all breached limits in stable policy order.

    Exact integer-ratio comparisons prevent rounding from allowing an exposure
    above a hard limit. All financial inputs/outputs remain finite Decimal values;
    neither caller Decimal settings nor binary floats enter these comparisons.
    """
    try:
        context = RiskContext.model_validate(context)
        config = RiskConfig.model_validate(config)
        quantity = context.requested_quantity
        reasons: list[RiskReason] = []
        if config.max_position_quantity is not None and quantity > config.max_position_quantity:
            reasons.append(RiskReason.MAX_POSITION_QUANTITY)
        # Evaluate only configured controls: disabled limits do no arithmetic.
        if config.max_notional_exposure is not None or config.max_equity_fraction is not None:
            notional = Fraction(context.reference_price) * Fraction(quantity)
            if (config.max_notional_exposure is not None
                    and notional > Fraction(config.max_notional_exposure)):
                reasons.append(RiskReason.MAX_NOTIONAL_EXPOSURE)
            if (config.max_equity_fraction is not None
                    and notional > Fraction(context.current_equity) * Fraction(config.max_equity_fraction)):
                reasons.append(RiskReason.MAX_EQUITY_FRACTION)
        if config.minimum_equity is not None and context.current_equity <= config.minimum_equity:
            reasons.append(RiskReason.MINIMUM_EQUITY)
        # equity / peak - 1 <= -limit, compared without rounded division.
        if config.max_drawdown_fraction is not None:
            peak = Fraction(context.running_peak_equity)
            equity = Fraction(context.current_equity)
            if peak - equity >= peak * Fraction(config.max_drawdown_fraction):
                reasons.append(RiskReason.MAX_DRAWDOWN)
        return RiskDecision(**context.model_dump(),
            action=RiskAction.REJECT if reasons else RiskAction.ALLOW,
            approved_quantity=Decimal("0") if reasons else quantity, reasons=tuple(reasons))
    except (ValueError, TypeError) as exc:
        raise RiskInputError(f"invalid entry risk input: {exc}") from exc
