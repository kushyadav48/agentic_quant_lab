# Feature engine

## Purpose and boundary

Phase 6 computes deterministic, provider-neutral features from canonical MarketBar
sequences. It does not execute strategies, evaluate rules, generate signals,
backtest, train models, place orders or access the network. Python 3.11+, the
standard library and existing Pydantic are sufficient.

## Contracts and input validation

The public API lives in quantlab.features. FeatureRequest declares an output
feature_id, optional implementation_id, immutable typed integer parameters, and
optional timeframe constraint. There are no implicit parameter defaults.
implementation_id permits explicit aliases such as sma_fast and sma_slow to select
the registered sma algorithm with different periods. Duplicate output identities
within a batch are rejected. Algorithm and parameters are retained in each output.

FeatureObservation is a strict frozen Pydantic model containing instrument_id,
feature_id, timestamp, available_at, finite Decimal value, timeframe, price_type,
implementation_id, parameters and input_start. Collections are tuples; extra
fields, floats and naive timestamps are rejected. Aware timestamps normalize to
UTC. JSON round trips retain typed semantics. input_start records the earliest
contributing bar start, not a content hash. Source/dataset lineage remains with
the caller's canonical input dataset.

compute_features requires explicit Instrument metadata and reuses Phase 4
validate_dataset. It revalidates bars, including unchecked Pydantic copies.
Inputs must be canonical bars with one instrument, timeframe and price basis,
strictly increasing unique starts, non-overlapping intervals, valid OHLC, finite
positive prices and valid availability. Multiple source/dataset references are
allowed. There is no sorting, repair, mutation, filling or calendar inference.
Gaps are allowed; windows count observed bars and returns span adjacent observed
closes. Callers needing session coverage should use Phase 4 scheduled validation.

## Causality and availability

The feature timestamp is the ending bar's **end_time**, including raw OHLC.
MarketBar represents a complete bar; its open is not exposed early here.
available_at is the maximum availability of every contributing bar, which also
covers the ending bar end. Availability may exceed subsequent event timestamps;
consumers must filter by available_at, not only by timestamp.

Raw features depend on one bar; returns on two adjacent bars; SMA on period bars;
volatility on window + 1 closes for window returns. EMA and RSI depend on their
seed and all subsequent recursive history: availability is a prefix maximum,
not just the last period. A delayed seed never expires from recursive availability.
Adding future bars leaves earlier values, identities and availability unchanged.
Early observations are omitted rather than NaN, zero-filled or forward-filled.

## Supported features and formulas

Indicators use close. Raw extraction retains the exact input Decimal. Calculated
values use an isolated Decimal Context with precision 34 and ROUND_HALF_EVEN,
independent of caller precision, rounding, traps and flags. Division, recursion,
Decimal.ln and Decimal.sqrt use that context; no float conversion is involved.
Values are not rounded to tick grids. Finite-precision rounding occurs at each
operation. Unsupported Decimal exponent overflow raises an error rather than
silently producing a domain observation.

| Registry ID | Parameters | First output | Formula / output |
| --- | --- | --- | --- |
| open, high, low, close | none | bar 1 | Corresponding complete-bar price |
| simple_return | none | bar 2 | close[t] / close[t-1] - 1; fractional return |
| log_return | none | bar 2 | ln(close[t] / close[t-1]); natural logarithm |
| sma | period integer >= 1 | bar period | Sum of last period closes / period |
| ema | period integer >= 1 | bar period | SMA seed; alpha * close[t] + (1-alpha) * EMA[t-1]; alpha = 2 / (period+1) |
| rsi | period integer >= 1 | bar period+1 | Wilder smoothing; index [0,100] |
| rolling_volatility | window integer >= 2 | bar window+1 | Population standard deviation of simple returns |

Boolean, float and Decimal parameters do not substitute for strict integers.
Missing, unknown, duplicate or extra parameters are errors. Invalid prices are
rejected before any return calculation, even on unchecked model copies.

EMA initializes with the arithmetic average of the first period closes and emits
that seed at the period-th bar. With period=1 it tracks close. State is local to
each batch, never persisted or shared.

For RSI, delta[t] = close[t] - close[t-1], gain = max(delta,0), and
loss = max(-delta,0). Initial averages are arithmetic means of period changes,
requiring period+1 bars. Subsequent smoothing and output are:

- avg_gain[t] = (avg_gain[t-1] * (period-1) + gain[t]) / period
- avg_loss[t] = (avg_loss[t-1] * (period-1) + loss[t]) / period
- RSI = 100 - 100 / (1 + avg_gain / avg_loss)

Both averages zero produces RSI=50; only avg_loss zero produces 100; only avg_gain
zero produces 0. Intermediate rounding may change final Decimal digits relative
to exact rational values.

Volatility uses N adjacent simple returns: mean = sum(returns) / N,
variance = sum((return - mean)^2) / N, volatility = sqrt(variance).
The divisor is N, not N-1. Output is unannualized, with no assumed trading-day
count. It does not use log returns.

## Registry and pipeline

FeatureRegistry exposes a read-only mapping of immutable FeatureDefinition
objects: IDs, kinds, required fields, parameter names/bounds, warm-up/output
semantics and optional timeframe constraint. get rejects unknown IDs; validate
checks requests. FeatureDefinition.required_bars describes minimum history for
a validated request. The fixed catalogue connects to the deterministic dispatcher
in indicators.py. There are no dynamic plugins, code strings, module loading,
registration side effects or executable DSLs.

The pipeline validates inputs and requests, shares simple returns where needed,
and caches identical algorithm/parameter computations within each batch. Output
is an immutable tuple ordered by (timestamp, feature_id), independent of request
order. Empty input returns an empty tuple after request validation; insufficient
history omits observations. Empty requests still validate bars.

With instrument and bars already available:

~~~python
from quantlab.features import FeatureParameter, FeatureRequest, compute_features

observations = compute_features(
    bars,
    (
        FeatureRequest(feature_id="close"),
        FeatureRequest(feature_id="sma", parameters=(
            FeatureParameter(name="period", value=20),
        )),
        FeatureRequest(feature_id="sma_fast", implementation_id="sma", parameters=(
            FeatureParameter(name="period", value=5),
        )),
    ),
    instrument=instrument,
)
~~~

## StrategySpecification compatibility

validate_feature_reference(reference, timeframe=...) returns a typed request;
validate_strategy_features(specification) returns requests for its declarations.
These helpers revalidate Phase 5 contracts, require exact supported calculation IDs,
valid arguments and feature_type=INDICATOR. That category covers all current
numeric features including raw extraction. ML_SIGNAL and LEVEL are unsupported.

FeatureReference.feature_id is the unique strategy-visible output alias; its
optional implementation_id identifies the registered calculation. The compatibility
helper maps both fields directly into FeatureRequest. When implementation_id is
None, registry lookup falls back to feature_id for backward compatibility. A single
strategy can declare fast_sma -> sma(period=2) and slow_sma -> sma(period=4),
preserving distinct output identities while using the same calculation. Unknown
implementation IDs are rejected. Names such as sma_20 are not parsed automatically:
they require an explicit implementation_id="sma" binding.

No override or a matching timeframe override is supported. A different timeframe
is rejected; there is no implicit resampling, cross-timeframe joining or availability
alignment. Already canonical D1/W1 bars are accepted because windows count bars
and use explicit endpoints; this adds no daily/weekly resampling or calendar logic.

Compatibility does not evaluate rules, check stop-distance units, approve a
strategy, establish execution eligibility, or change its digest/state. Feature
price distances need further unit validation before execution.

## Limitations

ATR and true range are deferred. No streaming state, DAG, incremental updates,
persistence, revised-data policy, cross-instrument/timeframe calculation,
annualization, optimization, ML preprocessing or strategy execution is included.
Batch computation favors inspectable correctness; performance tuning and feature
artifact versioning remain future work. Fixed 34-digit calculations follow the
documented precision policy rather than arbitrary-precision exact arithmetic.
