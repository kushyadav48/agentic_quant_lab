"""Immutable Phase 9 reports; financial ratios are Decimal fractions."""
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field
from quantlab.backtesting.models import FiniteDecimal
from quantlab.data import PriceType, Timeframe
from quantlab.data.models import (
    Identifier, NonNegativeDecimal, PositiveDecimal, UtcTimestamp, _DomainModel,
)
from quantlab.strategies.schema import Digest

Count = Annotated[int, Field(ge=0)]
Rate = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
NonPositiveDecimal = Annotated[Decimal, Field(le=0, allow_inf_nan=False)]
Duration = Annotated[timedelta, Field(ge=timedelta(0))]


class AnalyticsConfig(_DomainModel):
    risk_free_rate_per_period: FiniteDecimal = Decimal("0")
    annualization_factor: PositiveDecimal | None = None


class TradeStatistics(_DomainModel):
    closed_trade_count: Count
    winning_trade_count: Count
    losing_trade_count: Count
    breakeven_trade_count: Count
    win_rate: Rate | None
    loss_rate: Rate | None
    gross_profit: NonNegativeDecimal
    gross_loss: NonNegativeDecimal
    net_closed_trade_pnl: FiniteDecimal
    average_trade: FiniteDecimal | None
    average_winner: PositiveDecimal | None
    average_loser: Annotated[Decimal, Field(lt=0, allow_inf_nan=False)] | None
    largest_winner: PositiveDecimal | None
    largest_loser: Annotated[Decimal, Field(lt=0, allow_inf_nan=False)] | None
    profit_factor: NonNegativeDecimal | None
    average_holding_duration: Duration | None
    minimum_holding_duration: Duration | None
    maximum_holding_duration: Duration | None


class ReturnPoint(_DomainModel):
    timestamp: UtcTimestamp
    period_return: FiniteDecimal | None


class ReturnStatistics(_DomainModel):
    absolute_return: FiniteDecimal
    cumulative_return: FiniteDecimal
    series: tuple[ReturnPoint, ...]
    undefined_period_count: Count
    mean_period_return: FiniteDecimal | None
    return_volatility: NonNegativeDecimal | None
    period_sharpe: FiniteDecimal | None
    annualized_sharpe: FiniteDecimal | None


class DrawdownPoint(_DomainModel):
    timestamp: UtcTimestamp
    running_peak: PositiveDecimal
    absolute_drawdown: NonPositiveDecimal
    percentage_drawdown: NonPositiveDecimal


class DrawdownEpisode(_DomainModel):
    peak_time: UtcTimestamp | None
    peak_equity: PositiveDecimal
    start_time: UtcTimestamp
    trough_time: UtcTimestamp
    recovery_time: UtcTimestamp | None
    absolute_drawdown: NonPositiveDecimal
    percentage_drawdown: NonPositiveDecimal
    duration: Duration


class DrawdownStatistics(_DomainModel):
    series: tuple[DrawdownPoint, ...]
    maximum_absolute_drawdown: NonPositiveDecimal
    maximum_drawdown_percentage: NonPositiveDecimal
    maximum_absolute_drawdown_time: UtcTimestamp | None
    maximum_percentage_drawdown_time: UtcTimestamp | None
    episodes: tuple[DrawdownEpisode, ...]
    longest_recovered_drawdown_duration: Duration | None
    current_drawdown_duration: Duration | None


class PerformanceReport(_DomainModel):
    definition_version: Literal["phase9-v1"] = "phase9-v1"
    strategy_id: Identifier
    strategy_version: Annotated[int, Field(ge=1)]
    strategy_content_digest: Digest
    instrument_id: Identifier
    timeframe: Timeframe
    price_type: PriceType
    config: AnalyticsConfig
    initial_capital: PositiveDecimal
    final_equity: FiniteDecimal
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal
    has_open_position: bool
    trades: TradeStatistics
    returns: ReturnStatistics
    drawdown: DrawdownStatistics
