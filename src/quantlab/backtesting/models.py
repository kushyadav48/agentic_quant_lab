"""Frozen research events and results; no brokerage cash/margin semantics."""
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator
from quantlab.data import PriceType, Timeframe
from quantlab.data.models import Identifier, PositiveDecimal, UtcTimestamp, _DomainModel
from quantlab.strategies.schema import Digest
from .enums import PositionSide, SignalAction

FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class BacktestConfig(_DomainModel):
    initial_capital: PositiveDecimal
    quantity: PositiveDecimal


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
    quantity: PositiveDecimal

    @model_validator(mode="after")
    def execution_timing(self) -> Self:
        # Adjacent half-open bars share the close/open instant, but not the bar.
        if self.execution_time < self.signal_time:
            raise ValueError("execution cannot precede signal")
        return self


class Position(_DomainModel):
    side: PositionSide
    quantity: PositiveDecimal
    entry_signal_time: UtcTimestamp
    entry_time: UtcTimestamp
    entry_price: PositiveDecimal

    @model_validator(mode="after")
    def entry_timing(self) -> Self:
        if self.entry_time < self.entry_signal_time:
            raise ValueError("entry cannot precede signal")
        return self


class ClosedTrade(Position):
    exit_signal_time: UtcTimestamp
    exit_time: UtcTimestamp
    exit_price: PositiveDecimal
    gross_pnl: FiniteDecimal

    @model_validator(mode="after")
    def exit_timing(self) -> Self:
        if self.exit_signal_time <= self.entry_time or self.exit_time < self.exit_signal_time:
            raise ValueError("exit signal must follow entry; execution must follow signal")
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
