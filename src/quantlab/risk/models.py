"""Immutable single-instrument entry limits, context and audit decisions."""
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator
from quantlab.data.models import NonNegativeDecimal, PositiveDecimal, UtcTimestamp, _DomainModel

FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
PositiveFraction = Annotated[Decimal, Field(gt=0, le=1, allow_inf_nan=False)]


class RiskAction(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"


class RiskSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class RiskReason(StrEnum):
    MAX_POSITION_QUANTITY = "max_position_quantity"
    MAX_NOTIONAL_EXPOSURE = "max_notional_exposure"
    MAX_EQUITY_FRACTION = "max_equity_fraction"
    MINIMUM_EQUITY = "minimum_equity"
    MAX_DRAWDOWN = "max_drawdown"


class RiskConfig(_DomainModel):
    """None disables each limit. Defaults impose no additional restrictions."""
    max_position_quantity: PositiveDecimal | None = None
    max_notional_exposure: PositiveDecimal | None = None
    max_equity_fraction: PositiveFraction | None = None
    minimum_equity: NonNegativeDecimal | None = None
    max_drawdown_fraction: PositiveFraction | None = None


class RiskContext(_DomainModel):
    """Known pre-fill flat account state, never the execution bar's close."""
    signal_time: UtcTimestamp
    execution_time: UtcTimestamp
    side: RiskSide
    requested_quantity: PositiveDecimal
    reference_price: PositiveDecimal
    current_equity: FiniteDecimal
    running_peak_equity: PositiveDecimal

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.execution_time < self.signal_time:
            raise ValueError("risk execution time cannot precede signal")
        if self.running_peak_equity < self.current_equity:
            raise ValueError("running peak must include current pre-entry equity")
        return self


class RiskDecision(RiskContext):
    """V1 approves the complete fixed quantity or rejects it entirely."""
    action: RiskAction
    approved_quantity: NonNegativeDecimal
    reasons: tuple[RiskReason, ...] = ()

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("risk reasons must be unique")
        if self.action is RiskAction.ALLOW:
            if self.reasons or self.approved_quantity != self.requested_quantity:
                raise ValueError("ALLOW requires full requested quantity and no reasons")
        elif not self.reasons or self.approved_quantity != 0:
            raise ValueError("REJECT requires reasons and zero approved quantity")
        return self
