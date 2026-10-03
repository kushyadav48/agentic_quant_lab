# Performance analytics (Phase 9)

Phase 9 measures completed historical simulation output. The offline
quantlab.analytics package consumes BacktestResult and produces an immutable,
auditable PerformanceReport. It does not evaluate rules, create signals, modify
fills/trades, force-close positions or change execution. These metrics do not
establish strategy quality, robustness or persistent profitability.

## Usage and architecture

~~~python
from decimal import Decimal
from quantlab.analytics import AnalyticsConfig, analyze_performance

# result is the completed BacktestResult returned by run_backtest.
report = analyze_performance(result)
configured = analyze_performance(result, AnalyticsConfig(
    risk_free_rate_per_period=Decimal("0.0001"),
    annualization_factor=Decimal("252"),  # caller assumption, never a default
))
wire = report.model_dump_json()
assert type(report).model_validate_json(wire) == report
~~~

models.py defines reports/configuration. returns.py and drawdown.py expose
compute_return_statistics(initial_capital, equity_curve, config=None) and
compute_drawdown_statistics(initial_capital, equity_curve). performance.py
combines these with closed-trade metrics in analyze_performance. Private
_validation.py checks inputs; errors.py distinguishes malformed input from
undefined metrics. No dependency is added; backtesting does not import analytics.

PerformanceReport records definition_version="phase9-v1", strategy identity,
version/content digest, instrument, timeframe, price basis and analytics config.
It retains initial_capital, final_equity, realized_pnl, unrealized_pnl and
has_open_position, plus nested trades, returns and drawdown statistics.
It adds no current timestamp or environment-derived identifier.

P&L units remain those of Phase 7/8 research accounting. BacktestResult supplies
no account currency, so analytics invents no currency label, conversion, contract
multiplier or brokerage valuation.

## Account performance and open positions

Account performance uses authoritative final equity, not a trade sum:

- absolute_return = final_equity - initial_capital.
- cumulative_return = final_equity / initial_capital - 1.

Ratios are Decimal fractions: 0.142 represents 14.2%. Positive initial capital
makes total return defined even when final equity is zero or negative.

Closed-trade statistics use only ClosedTrade.net_pnl. Spread/slippage already
worsen execution prices; commissions/fees are subtracted in net_pnl. Analytics
never charges them again. gross_profit/gross_loss below aggregate winning/losing
**net** trades, rather than ClosedTrade.gross_pnl or reference_gross_pnl.

A final position stays open. Its close-marked unrealized P&L contributes to final
equity; entry commission/fees already reduce realized account P&L. No future exit
price, fill or cost is invented. Closed-trade net P&L can therefore differ from
realized account P&L and total account change. For the Phase 8 closed-plus-open
example: closed net=11, realized=9.5, unrealized=17, final equity=1026.5,
absolute return=26.5 and cumulative return=0.0265.

## Closed-trade statistics and holding times

Let N be the closed-trade count. Positive net_pnl is a winner, negative a loser,
and zero breakeven.

| Field | Definition |
| --- | --- |
| closed_trade_count | N |
| winning_trade_count / losing_trade_count / breakeven_trade_count | Class counts |
| win_rate / loss_rate | Class count / N; breakevens remain in N |
| gross_profit | Sum of positive closed-trade net_pnl |
| gross_loss | Absolute sum of negative closed-trade net_pnl |
| net_closed_trade_pnl | Sum of all closed-trade net_pnl in stored order |
| average_trade | net_closed_trade_pnl / N |
| average_winner | gross_profit / winning_trade_count |
| average_loser | -gross_loss / losing_trade_count; negative |
| largest_winner | Maximum positive net_pnl |
| largest_loser | Minimum negative net_pnl; most negative loss |
| profit_factor | gross_profit / gross_loss when gross_loss > 0 |

Holding time is exit_time - entry_time, using execution timestamps, not signal
times or bar counts. Minimum, maximum and arithmetic average use closed trades
only. Average duration uses exact integer microseconds, rounded to the nearest
whole microsecond, ties to even, to fit timedelta. Pydantic serializes ISO
durations. Gaps count as elapsed time; no session adjustment is made.

## Equity returns, volatility and Sharpe

Each EquityPoint is the retrospective bar-end mark, after any next-open fill.
Start previous_equity=initial_capital. At every point:

period_return = equity / previous_equity - 1

Then set previous_equity=equity, including after an undefined period. The first
mark is included; engine output normally starts with zero because a first-bar
signal cannot fill on the same bar. A first mark below capital contributes an
immediate loss. Returns describe consecutive observations, not guaranteed equal
wall-clock intervals. Gaps are not filled. Cumulative return is capital-relative
ending return, rather than compounding rounded period returns.

previous_equity <= 0 makes that period_return=None. A decline from positive
equity to zero/negative equity remains defined. All timestamps stay in the series;
undefined_period_count records absent ratios. If any period is undefined, full
series mean, volatility and both Sharpe values are None. Undefined periods are
never silently discarded. Later positive previous equity permits later returns.

For N valid returns:

- mean_period_return = sum(r) / N.
- population variance = sum((r - mean_period_return) ** 2) / N.
- return_volatility = Decimal sqrt(population variance).

The divisor is N, never N-1. One return has zero population volatility. With no
observations, mean/volatility are None. The pure helper accepts an empty curve
with unchanged capital and zero total return; analyze_performance requires the
nonempty curve produced by the backtester.

AnalyticsConfig defaults risk_free_rate_per_period to Decimal zero. It is a
per-observation fractional return, not an annual interest rate:

- excess_t = period_return_t - risk_free_rate_per_period.
- mean_excess = sum(excess_t) / N.
- excess_volatility = sqrt(sum((excess_t - mean_excess) ** 2) / N).
- period_sharpe = mean_excess / excess_volatility if excess_volatility > 0.
- annualized_sharpe = period_sharpe * sqrt(annualization_factor), only if supplied.

Excess statistics are calculated directly in the isolated Decimal context.
Constant subtraction leaves population volatility mathematically unchanged,
subject to finite-precision arithmetic. Zero excess volatility makes both Sharpe
values None. annualization_factor must be a finite positive Decimal; the
risk-free value must be a finite Decimal and may be negative. Timeframe, Forex
sessions, holidays and machine timezone never imply 252 or any other factor.
With irregular observations/gaps, callers own interpretation of an explicit factor.

## Drawdowns, recovery and durations

Initial capital participates in the high-water mark before the first observation:

- running_peak_t = max(initial_capital, equity_1, ..., equity_t).
- absolute_drawdown_t = equity_t - running_peak_t.
- percentage_drawdown_t = equity_t / running_peak_t - 1.

The peak is positive because initial capital is positive. Drawdowns are signed,
nonpositive values. Negative equity can produce percentage drawdown below -1.
Series points retain timestamps, peaks and both drawdown measures.
maximum_absolute_drawdown and maximum_drawdown_percentage are the respective
series minima (zero without drawdowns). Each has its own trough timestamp:
greatest monetary/proportional losses can occur at different peaks. Equal
maximum/trough ties retain the earliest observation.

An episode starts at the first observed equity below the previous running peak.
peak_time is the latest observation attaining that peak; equal peaks refresh it.
If only the initial capital baseline establishes the peak, peak_time=None because
BacktestResult supplies no initial-capital timestamp. Episodes retain peak equity,
first underwater time, deepest trough/time and drawdowns.

Recovery is the first observation with equity >= the episode peak, including exact
equality. recovery_time=None for an unrecovered final episode. Duration is elapsed
time **from the first underwater observation**, not peak_time, to recovery or the
final mark. This is observed time, not estimated intrabar onset/recovery. A single
final underwater observation has duration zero. longest_recovered_drawdown_duration
is the maximum recovered duration, or None if none recovered.
current_drawdown_duration is the final unrecovered duration, or None if recovered.
End of data never fabricates recovery.

## Undefined values and validation

| Condition | Behavior |
| --- | --- |
| No closed trades | Counts/sums=0; rates, averages, extrema, profit factor and holding durations=None |
| No winners | average_winner/largest_winner=None; gross_profit=0 |
| No losers | average_loser/largest_loser=None; gross_loss=0; profit_factor=None |
| Only losing trades | profit_factor=0; win_rate=0 |
| All breakeven | Rates/average_trade=0; winner/loser averages/extrema and profit_factor=None |
| Empty pure return series | Mean/volatility/Sharpe=None; total return=0 |
| Nonpositive previous equity | Affected returns=None; full-series mean/volatility/Sharpe=None |
| Zero volatility | Volatility=0; Sharpe=None |
| No annualization factor | annualized_sharpe=None |
| No drawdown/recovery | Maxima=0; absent episode durations/timestamps=None |

No Infinity or fabricated zero replaces an undefined ratio. Public models follow
strict, frozen, extra-forbidden, always-revalidate conventions with finite financial
Decimals, immutable tuples and typed JSON round trips. Invalid direct construction
raises Pydantic ValidationError. Public calculations raise AnalyticsInputError
(subclass of AnalyticsError and ValueError) for malformed/unrepresentable input.
Ordinary undefined metrics return None.

analyze_performance revalidates BacktestResult and nested contracts, including
unchecked copies. It verifies a nonempty strictly chronological curve, each
point's capital + realized + unrealized identity, the final account identity,
and agreement of all ending account values with the final mark. It checks flat
unrealized P&L is zero, fixed trade/position quantities match, and closed/open
position times follow one-position ordering. Pure helpers require a tuple of
canonical EquityPoint objects and enforce schema/chronology/equity identities.
Input is never repaired, sorted or mutated.

The boundary does not reconstruct fills, trades or accounting. It does not require
independently rounded trade net P&L sums to equal realized P&L: open entry costs
and nonassociative finite-precision event-ordered accounting can make that false.
The supplied simulation ledger remains authoritative. An internally coherent
fabricated ledger is not proof that a simulation ran.

## Determinism, tests and limitations

All arithmetic uses fresh isolated Decimal Context(prec=34, rounding=ROUND_HALF_EVEN),
including native sqrt, independent of caller precision, rounding, traps and flags.
Operations round at that precision; unsupported exponent overflow is an explicit
input error. No binary float enters financial calculations or duration averages.
Same result/config produces equal reports and identical JSON. No network,
randomness, current timestamps or mutable global calculation state enters results.

Hand-computed tests cover trade classes, costs, open positions, returns, population
deviation, Sharpe/configuration, initial drawdowns, independent maxima, recovery,
unrecovered episodes and durations. Replay, strict models, JSON, immutability,
hostile Decimal contexts, offline imports/calculations and malformed copies are tested.

No Sortino, Calmar, CAGR, exposure model, benchmark, calendar inference, portfolio,
ranking, recommendation, optimization, robustness testing, VaR/CVaR, risk controls,
ML/LLM/agents, API, frontend or paper trading is added. Valuation/execution retain
Phase 7/8 limitations. Historical metrics do not validate strategy quality or
future profitability. Phase 10 orchestration is implemented separately; see [research validation](research-validation.md). Later phases remain planned.
