# Deterministic backtesting engine

## Purpose and scope

Phase 7 provides a small offline bar-by-bar research simulator in
quantlab.backtesting. It consumes canonical MarketBar sequences, supplied
Phase 6 FeatureObservation values, an approved immutable StrategySpecification,
and a BacktestConfig. It returns a frozen BacktestResult with signals, fills,
closed trades, an optional final position, and a research equity curve.

The engine supports LONG, SHORT and BOTH, fixed quantity, one position, declarative
rules and next-open market fills. It adds no dependencies or network access.
Performance ratios, drawdown, optimization, portfolio allocation, broker execution,
paper trading, risk models and execution realism belong to later phases.

## Entry point and input contracts

~~~python
from decimal import Decimal
from quantlab.backtesting import BacktestConfig, run_backtest
from quantlab.features import compute_features, validate_strategy_features

# approved_strategy, instrument and bars are caller-supplied canonical inputs.
# Review and approve the exact specification using the Phase 5 contract first.
bars = tuple(bars)
features = compute_features(
    bars, validate_strategy_features(approved_strategy), instrument=instrument,
)
result = run_backtest(
    approved_strategy, bars, features, instrument=instrument,
    config=BacktestConfig(initial_capital=Decimal("10000"), quantity=Decimal("1000")),
)
wire = result.model_dump_json()
~~~

Instrument metadata is explicit, as in Phase 6. Initial capital and quantity must
be strictly typed, finite positive Decimal values. Quantity must be an exact
multiple of Instrument.quantity_increment. No minimum order size is invented;
lot_size is metadata, not an additional minimum. Quantity is a research unit for
the price-difference formulas below. Contract multiplier, leverage, cash debits,
margin and currency conversion are not part of this accounting convention.

The primary entry point revalidates the existing Phase 5 schema, including the
approval record binding to strategy ID, version and content digest. Only APPROVED
is accepted. DRAFT, VALIDATED and revisions that dropped approval are rejected.
The engine never promotes, approves, alters or mutates a specification. Approval
records review of the exact interpretation; they imply no profitability or
robustness and do not authenticate the reviewer.

Phase 7 requires the strategy to declare exactly the supplied single instrument
and a matching timeframe. Non-None stop_loss, take_profit, session and
sizing_reference are compatibility errors. Stops and targets need intrabar/gap
ordering; sessions need calendar and evaluation policies; external sizing needs
later integration. None of these intents is silently ignored. BacktestConfig
quantity is the sole sizing mechanism.

Bar validation reuses Phase 4 validate_dataset: canonical model objects only,
valid OHLC and timestamps, one instrument/timeframe/price type, increasing unique
starts and no overlapping intervals. Unchecked Pydantic copies are revalidated.
Gaps and multiple source/dataset references are allowed. A gap does not fabricate
bars: the next supplied bar supplies the next execution open and offsets count
observed bars. Session coverage remains caller-owned. Empty bars raise
BacktestInputError. One bar is accepted and may produce an unfilled signal.

Feature compatibility reuses validate_strategy_features and its fixed Phase 6
registry. Only supported INDICATOR declarations are accepted; ML_SIGNAL, LEVEL,
unknown algorithms, invalid arguments and differing timeframe overrides fail.
Feature IDs are strategy-visible aliases; implementation_id identifies the
calculation, falling back to feature_id when the declaration omits it.

Supplied observations must be canonical, schema-valid and chronological by
timestamp (order among different IDs at equal timestamps is immaterial). Duplicate
(feature_id, timestamp), undeclared IDs, wrong instrument/timeframe/price type,
or declaration algorithm/parameter mismatches fail. Where supplied bars expose
feature dependencies, availability cannot precede a contributing bar; input_start
must include the ending bar when its timestamp matches. Values are consumed as
supplied rather than recomputed or proved numerically correct. Callers retain
provenance and should compute them using Phase 6. Observations outside the tested
bar range may be supplied; they cannot match an earlier exact timestamp lookup.
No sorting, repair, forward filling or input mutation occurs.

## Rule evaluation

RuleEvaluator is the low-level evaluator over inputs already validated by the
engine. Its evaluate(rule_or_group, index) returns EvaluationResult:

| Result | Meaning |
| --- | --- |
| TRUE | Required operands are available and the predicate matches. |
| FALSE | Required operands are available and the predicate does not match. |
| UNAVAILABLE | Required history or timely data is missing. |

Only TRUE generates a signal. Missing data does not become False, zero or a
truthy/falsy value. For ALL, any FALSE wins; otherwise any UNAVAILABLE wins;
otherwise TRUE. For ANY, any TRUE wins; otherwise any UNAVAILABLE wins;
otherwise FALSE. Phase 5 groups are bounded flat collections, not nested trees.

Operands resolve as follows:

- MarketOperand supports OPEN/HIGH/LOW/CLOSE from MarketBar. BID/ASK are rejected
  even when located in exit rules: OHLC bars cannot supply quote operands.
- FeatureOperand resolves the exact (feature_id, target_bar.end_time) observation.
  Phase 6 timestamps always represent the ending bar end. Missing warm-up or an
  absent timestamp remains UNAVAILABLE; no nearest older observation substitutes.
- ParameterOperand resolves the declared default without overrides or coercion.
- ConstantOperand retains its strict integer, Decimal or boolean value.

Market/feature offset=0 selects the evaluation bar, offset=1 the preceding input
bar, and so forth. Missing history remains UNAVAILABLE, never clamped. GT, GE,
LT, LE and EQ compare resolved values. Numeric integer/Decimal comparisons retain
Python numeric semantics; booleans follow Phase 5's equality-with-boolean-only
contract. There is no eval, exec, expression language or user callback.

Crossings require current and previous values on both sides:

- A CROSSES_ABOVE B: current A > current B and previous A <= previous B.
- A CROSSES_BELOW B: current A < current B and previous A >= previous B.

Previous means resolving the same operands one bar earlier. Thus an operand with
offset=1 uses N-1 on the current crossing side and N-2 on the previous side.
If any required value is missing, the crossing is UNAVAILABLE. Prior values and
current values must all be available by the current decision time. A historical
value delayed until the current close may participate now; the earlier missed
decision is never replayed or revised.

## Causality and timing

Decision time is exactly the current bar end_time (BAR_CLOSE). The complete
current bar must be available by that time; if it is delayed, the whole decision
is UNAVAILABLE, including rules using only constants or prior offsets. All used
market operands and feature observations must also satisfy available_at <= this
decision time. Bar-relative feature timestamps cannot exceed the evaluation bar
end. Future observations, features at the wrong timestamp, and data published
later cannot change historical signals.

A TRUE entry/exit at bar N close records a Signal with exact strategy identity,
version/digest, source bar interval, instrument, action and signal time. It creates
one pending action. That action fills at bar N+1 open, before evaluating N+1 close.
The engine never uses bar N open after inspecting bar N close.

Adjacent half-open bars can share bar N end_time and bar N+1 start_time. Equal
signal and execution timestamps are therefore legitimate: ordering and bar
identity distinguish close decision from the following open. Gaps use the next
available input bar's start.

Fills at the next open are a declared ideal execution assumption. They do not
require the complete execution bar to have been published at its start; its later
publication still gates its close decision. Phase 7 does not simulate publication
latency of the opening price. Equity marks use historical closes retrospectively,
including delayed bars, and are not inputs to strategy decisions or sizing.

On the final bar, signals are recorded but have no fill. There is no invented next
bar, end-of-data liquidation or final close execution.

## Position lifecycle

While flat, evaluate the direction's entry group(s). Exactly one TRUE side opens
a pending entry. No TRUE means remain flat. BOTH with simultaneous long/short
TRUE raises BacktestSignalConflictError, including on the final bar.

While long, evaluate only long.exit; while short, only short.exit. exit=None holds
the position. An exit TRUE creates a pending close for the following open.
No entry evaluation occurs while a position exists, so there is no pyramiding or
duplicate entry. After an open-time close, a new entry may be signalled at that
bar's future close and fill at the following open. Closing and reversing at the
same open is unsupported.

Public Position captures side, quantity, entry signal time, execution time and
price. ClosedTrade retains those fields plus exit signal/time/price and gross
P&L. Public objects are frozen; only tightly scoped local loop state changes.

## Research accounting

For a fixed quantity Q:

- LONG unrealized P&L = (close mark - entry price) * Q.
- SHORT unrealized P&L = (entry price - close mark) * Q.
- LONG gross closed P&L = (exit price - entry price) * Q.
- SHORT gross closed P&L = (entry price - exit price) * Q.
- equity = initial_capital + cumulative_realized_pnl + current_unrealized_pnl.

Each processed bar records one EquityPoint at its end, after pending execution
and close-time rule evaluation. Flat unrealized P&L is zero. Capital is not debited
on entry; negative equity is permitted without an invented liquidation policy.
This is deliberately simple research-equity accounting, not broker cash/margin
accounting. Open positions are returned at end of data with their final close mark
reflected in unrealized P&L and final equity, separate from closed trades.

Financial arithmetic uses an isolated Decimal Context with precision 34 and
ROUND_HALF_EVEN, independent of caller precision, rounding, traps and flags.
Operations round at that precision and unsupported exponent overflow raises an
error. Quantity-step checking uses exact integer ratios. No float arithmetic,
wall-clock times, randomness or environment-dependent IDs enter results.
Tuples preserve event order, and identical typed inputs produce equal results and
identical model_dump_json output. Public models support typed JSON round trips.

## Hand-computed example

With capital 1000, quantity 2, long entry close > 100 and exit close < 100:

| Bar | Open | Close | Close decision | Open fill | Research equity |
| --- | ---: | ---: | --- | --- | ---: |
| 0 | 80 | 101 | Enter long pending | none | 1000 |
| 1 | 100 | 110 | Hold long | Enter at 100 | 1020 |
| 2 | 111 | 99 | Exit long pending | none | 998 |
| 3 | 90 | 90 | Remain flat | Exit at 90 | 980 |

Closed gross P&L is (90 - 100) * 2 = -20. A short entered at 100 and marked
at 90 has unrealized P&L (100 - 90) * 2 = 20; closing at 90 realizes 20.
The tests use these exact Decimal examples and independent causal prefixes.

## Errors, limitations and Phase 8 boundary

BacktestError has three focused subclasses: BacktestCompatibilityError for
unsupported strategy intent/approval, BacktestInputError for malformed or
inconsistent data/config, and BacktestSignalConflictError for simultaneous entries.
Constructing invalid public models directly raises Pydantic ValidationError.

Every Phase 7 fill is exactly the next supplied bar open with zero spread,
slippage, commissions and fees. Bid/ask-based bars retain their declared price
basis but do not simulate switching bid/ask sides. No partial fills, advanced
orders, stop/target execution, financing, market impact, margin or leverage exists.

The small private _next_open_fill boundary in engine.py owns execution price and
Fill construction. Phase 8 can extend it with explicit execution-price/cost
behavior while preserving signal timing and position lifecycle. No full execution
framework or cost fields have been pre-built. Phase 9 will add analytics derived
from trades/equity; none are reported in this result.
