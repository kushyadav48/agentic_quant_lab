"""Frozen research events and results; no brokerage cash/margin semantics."""
from decimal import Decimal, localcontext
from typing import Annotated, Self

from pydantic import Field, model_validator
from quantlab._decimal import deterministic_context
from quantlab.data import PriceType, Timeframe
from quantlab.data.models import Identifier, NonNegativeDecimal, PositiveDecimal, UtcTimestamp, _DomainModel
from quantlab.strategies.schema import Digest
from quantlab.risk import RiskConfig, RiskDecision
from .enums import PositionSide, SignalAction

FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class ExecutionCostConfig(_DomainModel):
    """Fixed price-unit effects and explicit research-accounting costs per fill."""
    spread: NonNegativeDecimal = Decimal("0")
    slippage: NonNegativeDecimal = Decimal("0")
    commission_per_unit: NonNegativeDecimal = Decimal("0")
    fixed_fee_per_fill: NonNegativeDecimal = Decimal("0")


class CostBreakdown(_DomainModel):
    """Quantity-scaled monetary costs; price effects must not be debited again."""
    spread_cost: NonNegativeDecimal = Decimal("0")
    slippage_cost: NonNegativeDecimal = Decimal("0")
    commission: NonNegativeDecimal = Decimal("0")
    fees: NonNegativeDecimal = Decimal("0")

    @property
    def explicit_cost(self) -> Decimal:
        with localcontext(deterministic_context()):
            return self.commission + self.fees

    @property
    def total_cost(self) -> Decimal:
        with localcontext(deterministic_context()):
            return self.spread_cost + self.slippage_cost + self.commission + self.fees

    def plus(self, other: "CostBreakdown") -> "CostBreakdown":
        with localcontext(deterministic_context()):
            return CostBreakdown(**{name: getattr(self, name) + getattr(other, name)
                                   for name in type(self).model_fields})


class BacktestConfig(_DomainModel):
    initial_capital: PositiveDecimal
    quantity: PositiveDecimal
    execution_costs: ExecutionCostConfig = Field(default_factory=ExecutionCostConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)


class Signal(_DomainModel):
    strategy_id: Identifier
    strategy_version: int = Field(ge=1)
    strategy_digest: Digest
    instrument_id: Identifier
    action: SignalAction
    signal_time: UtcTimestamp
    source_bar_start: UtcTimestamp
    source_bar_end: UtcTimestamp

    @model_validator(mode="after")
    def close_timing(self) -> Self:
        if self.source_bar_start >= self.source_bar_end or self.signal_time != self.source_bar_end:
            raise ValueError("signal must be at its source bar close")
        return self


class Fill(_DomainModel):
    action: SignalAction
    signal_time: UtcTimestamp
    execution_time: UtcTimestamp
    execution_price: PositiveDecimal
    reference_price: PositiveDecimal
    spread_adjustment: NonNegativeDecimal = Decimal("0")
    slippage_adjustment: NonNegativeDecimal = Decimal("0")
    costs: CostBreakdown = Field(default_factory=CostBreakdown)
    quantity: PositiveDecimal

    @model_validator(mode="after")
    def execution_timing(self) -> Self:
        # Adjacent half-open bars share the close/open instant, but not the bar.
        if self.execution_time < self.signal_time:
            raise ValueError("execution cannot precede signal")
        with localcontext(deterministic_context()):
            buy = self.action in (SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT)
            expected = self.reference_price
            if self.spread_adjustment:
                expected = expected + self.spread_adjustment if buy else expected - self.spread_adjustment
            if self.slippage_adjustment:
                expected = expected + self.slippage_adjustment if buy else expected - self.slippage_adjustment
            if self.execution_price != expected:
                raise ValueError("execution price must match adverse spread/slippage adjustments")
            if (self.costs.spread_cost != self.spread_adjustment * self.quantity
                    or self.costs.slippage_cost != self.slippage_adjustment * self.quantity):
                raise ValueError("price-effect costs must equal adjustment times quantity")
            adverse_change = (self.execution_price - self.reference_price if buy
                              else self.reference_price - self.execution_price)
            if adverse_change * self.quantity != self.costs.spread_cost + self.costs.slippage_cost:
                raise ValueError("execution price effects cannot reconcile at precision 34")
        return self


class Position(_DomainModel):
    side: PositionSide
    quantity: PositiveDecimal
    entry_signal_time: UtcTimestamp
    entry_time: UtcTimestamp
    entry_price: PositiveDecimal
    entry_reference_price: PositiveDecimal
    entry_costs: CostBreakdown = Field(default_factory=CostBreakdown)

    @model_validator(mode="after")
    def entry_timing(self) -> Self:
        if self.entry_time < self.entry_signal_time:
            raise ValueError("entry cannot precede signal")
        with localcontext(deterministic_context()):
            change = (self.entry_price - self.entry_reference_price if self.side is PositionSide.LONG
                      else self.entry_reference_price - self.entry_price)
            if change * self.quantity != self.entry_costs.spread_cost + self.entry_costs.slippage_cost:
                raise ValueError("entry reference/execution prices must match entry price-effect costs")
        return self


class ClosedTrade(Position):
    exit_signal_time: UtcTimestamp
    exit_time: UtcTimestamp
    exit_price: PositiveDecimal
    gross_pnl: FiniteDecimal
    exit_reference_price: PositiveDecimal
    exit_costs: CostBreakdown = Field(default_factory=CostBreakdown)
    reference_gross_pnl: FiniteDecimal
    net_pnl: FiniteDecimal

    @property
    def costs(self) -> CostBreakdown:
        return self.entry_costs.plus(self.exit_costs)

    @model_validator(mode="after")
    def exit_timing(self) -> Self:
        if self.exit_signal_time <= self.entry_time or self.exit_time < self.exit_signal_time:
            raise ValueError("exit signal must follow entry; execution must follow signal")
        with localcontext(deterministic_context()):
            gross = ((self.exit_price - self.entry_price) if self.side is PositionSide.LONG
                     else (self.entry_price - self.exit_price)) * self.quantity
            reference = ((self.exit_reference_price - self.entry_reference_price)
                         if self.side is PositionSide.LONG
                         else (self.entry_reference_price - self.exit_reference_price)) * self.quantity
            costs = self.costs
            if self.gross_pnl != gross or self.reference_gross_pnl != reference:
                raise ValueError("gross P&L must match execution/reference prices and quantity")
            if self.net_pnl != self.gross_pnl - costs.commission - costs.fees:
                raise ValueError("net P&L must subtract explicit costs only")
            if self.net_pnl != self.reference_gross_pnl - costs.spread_cost - costs.slippage_cost - costs.commission - costs.fees:
                raise ValueError("reference P&L minus all costs must equal net P&L at precision 34")
        return self


class EquityPoint(_DomainModel):
    timestamp: UtcTimestamp
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal
    equity: FiniteDecimal


class BacktestResult(_DomainModel):
    strategy_id: Identifier
    strategy_version: int = Field(ge=1)
    strategy_content_digest: Digest
    instrument_id: Identifier
    timeframe: Timeframe
    price_type: PriceType
    initial_capital: PositiveDecimal
    quantity: PositiveDecimal
    signals: tuple[Signal, ...]
    fills: tuple[Fill, ...]
    closed_trades: tuple[ClosedTrade, ...]
    open_position: Position | None
    equity_curve: tuple[EquityPoint, ...]
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal
    final_equity: FiniteDecimal
    execution_costs: ExecutionCostConfig = Field(default_factory=ExecutionCostConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    risk_decisions: tuple[RiskDecision, ...] = ()
