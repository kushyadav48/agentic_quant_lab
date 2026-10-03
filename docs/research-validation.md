# Research validation (Phase 10)

Phase 10 is an offline orchestration layer in `quantlab.validation`. It evaluates
one fixed approved strategy on chronological holdouts and sequential folds, or
explicitly approved nearby parameter variants on one window. Backtesting simulates
one prepared dataset; analytics measures one completed result; validation coordinates
independent runs. No fitting, selection, ranking, optimization or automatic approval
occurs. “Train” means an in-sample evaluation window, not a fitted strategy.

## Holdout usage and membership

~~~python
from quantlab.validation import HoldoutConfig, ValidationWindow, run_holdout

# approved_strategy, canonical bars, causal features, instrument and config
# are caller-supplied. Include historical bars needed by feature input_start.
result = run_holdout(
    approved_strategy, bars, features, instrument=instrument, config=config,
    split=HoldoutConfig(
        train=ValidationWindow(start=0, end=100),
        test=ValidationWindow(start=100, end=140),
    ),
)
in_sample = result.in_sample.performance
out_of_sample = result.out_of_sample.performance
assert type(result).model_validate_json(result.model_dump_json()) == result
~~~

Every window is a nonempty half-open bar-index range `[start, end)` into the
supplied canonical sequence. Counts are observations, not minutes/days/sessions.
The last included index is end-1. Train must precede test without overlap;
adjacent indices are allowed. Unused prefixes, gaps between windows and suffixes
are allowed. No sorting, shuffling, forward filling or repair occurs. Existing
Phase 4/7 checks reject duplicates, overlaps, reversed bars and malformed inputs.

WindowMetadata records the exact indices, count and first/last decision closes
(MarketBar.end_time, normalized to UTC). holdout_windows exposes the boundaries
without executing a strategy. HoldoutResult retains separate in_sample and
out_of_sample SegmentResult records, each containing metadata, BacktestResult
and PerformanceReport. Original arrays are not embedded in the reports.

## Walk-forward windows

~~~python
from quantlab.validation import WalkForwardConfig, WalkForwardMode, run_walk_forward

report = run_walk_forward(
    approved_strategy, bars, features, instrument=instrument, config=config,
    walk_forward=WalkForwardConfig(
        train_size=100, test_size=20, step_size=20,
        mode=WalkForwardMode.EXPANDING,
    ),
)
summary = report.out_of_sample_summary
~~~

For fold k (zero-based), test_start = train_size + k * step_size and test_end =
test_start + test_size. Expanding train is [0, test_start); rolling train is
[test_start-train_size, test_start). Both modes are supported. Only complete test
windows are emitted; a partial final window is omitted. A request that cannot
produce one complete fold is rejected. walk_forward_folds exposes this plan.

Step smaller than test_size creates overlapping OOS windows; greater creates
gaps. These are explicit independent evaluations. Counts/trade totals count
repeated observations/trades when windows overlap; they are not unique portfolio
exposures. Each fold keeps its index, both window metadata and separate backtests
and analytics. fold_count derives from the immutable tuple. Same approved
specification is used for every train and test; no candidate is selected from train
performance and no subsequent test observation influences a train decision.

## Positions, boundaries and execution costs

Every segment and candidate starts flat, with the supplied initial capital and
fixed quantity. There is no train-to-test or fold-to-fold position/capital carry.
Open positions remain open at a segment's end; unrealized P&L remains part of its
own final equity. No closing trade, exit cost or recovery is fabricated.

Only the segment's bars reach run_backtest. A final-bar signal stays recorded,
but cannot fill unless its following execution bar is in that same segment.
The first test open cannot fill a final train signal; a suffix open cannot fill
a final test signal. This uses Phase 7 behavior without result filtering or an
engine change. All Phase 8 costs from the supplied BacktestConfig are retained,
including spread, slippage, per-unit commission and per-fill fees. The nested
BacktestConfig.risk is also preserved by every segment/fold/candidate. Each replay
starts with a fresh peak at its own initial capital; risk state never carries
between runs. Segment BacktestResult retains risk and ordered risk_decisions,
including rejected entries with their original signals and no fill/cost. Existing price
basis restrictions and accounting errors still apply.

## Explicit feature and warm-up policy

Callers provide already prepared canonical causal FeatureObservation records.
Phase 10 neither computes features nor fits preprocessing. Phase 6 compute_features
is suitable for the existing causal SMA/EMA/Wilder RSI implementations; callers
must not substitute observations fitted using future/test data. Metadata cannot
prove how an externally supplied numeric value was computed.

The full supplied chronological bar/feature history is revalidated using the
existing engine's strategy/input contract checks before segmentation. Supplied
bars must cover each observation's input_start; earlier uncovered history is a
compatibility error. Dependency availability is checked against historical bars,
including delayed bars before an evaluation window. This prevents slicing away
a delayed dependency before validation. Include the historical prefix in bars,
and place the evaluation window after it. No prefix bars are executed.

Only observations with an exact timestamp equal to a segment bar's end_time are
passed into its backtest, retaining their original value, parameters,
implementation identity, input_start and available_at. Causal historical lookback
may therefore supply an indicator at the first segment close without creating
pre-segment signals, trades or positions. Missing, off-grid or delayed values
remain unavailable; no nearest match, interpolation or forward fill is used.
Future observations never resolve at an earlier decision timestamp.

**Rule offsets and crossing history are segment-local.** The engine receives
only evaluation bars, so an offset before segment start and a crossing on the
first segment bar are unavailable, even if a prepared indicator exists there.
Those bars remain in the equity curve; decisions are not silently dropped.
Warm-up observations omitted by Phase 6 remain unavailable. Carrying prior rule
history into first-bar decisions would require a separate execution-window
contract and is deferred. Feature context is not execution context.

## Descriptive summaries and undefined analytics

Each segment uses analyze_performance with the exact supplied AnalyticsConfig
(defaulting to Phase 9's defaults). Analytics formulas, costs, open-position marks,
negative-equity behavior and undefined values are unchanged. No closed trades
means undefined trade ratios; zero return volatility means undefined Sharpe; a
nonpositive previous equity preserves undefined period returns. No missing ratio
is replaced by zero or discarded. See [Phase 9 policies](performance-analytics.md).

DescriptiveSummary is used for OOS folds and parameter candidates:

- evaluation_count; profitable/losing/breakeven counts based on capital-relative total return;
- mean, median, minimum and maximum total return, and maximum-minus-minimum range;
- sum of closed-trade counts across independent evaluations;
- mean, minimum and maximum maximum-drawdown percentages (nonpositive Phase 9 signs).

Total return is PerformanceReport.returns.cumulative_return, including unrealized
P&L. Median sorts Decimal values only: odd lengths use the middle value; even
lengths average the two middle values at the declared Decimal precision. No float
conversion occurs. Drawdown minimum is the most negative; maximum is the least
negative. No blended equity curve, compounded portfolio return, candidate score,
quality label, winner or recommended parameter set is produced.

## Parameter sensitivity and approval integrity

~~~python
from quantlab.validation import RobustnessCandidate, ValidationWindow, run_parameter_robustness

# Each specification has its own caller-supplied exact-version approval record.
report = run_parameter_robustness(
    (
        RobustnessCandidate(candidate_id="baseline", strategy=approved_baseline),
        RobustnessCandidate(candidate_id="nearby", strategy=approved_variant),
    ),
    bars, features, instrument=instrument, config=config,
    baseline_candidate_id="baseline", window=ValidationWindow(start=100, end=140),
)
~~~

Candidates are an explicit finite sequence, preserved in caller order. Identifiers
must be unique and the baseline must be explicitly present; there is no implicit
baseline or ranking. All variants share the same strategy ID and content structure,
parameter names/types/bounds/descriptions and feature definitions. Only declared
parameter defaults and revision provenance may differ. Feature argument changes
(e.g. an SMA period embedded in FeatureReference) are outside this V1 API; it tests
ParameterOperand sensitivity, not arbitrary structurally different strategies.
Candidates need no sorting by values. There is no grid generation or runtime override.

Use Phase 5 revise, then explicitly validate and approve the resulting variant
before submitting it. Revision clears prior approval. Strict Parameter checks enforce
exact integer/Decimal/boolean types and declared bounds. All nested specifications
and approvals are revalidated, then Phase 7 enforces APPROVED exact identity/version/
digest. Stale copied approvals and unapproved revisions are rejected. Validation
never constructs an approval or grants a modified strategy the baseline approval.
Candidate results retain the complete supplied specification, its parameter defaults,
version/approval/digest, and separate SegmentResult. The summary measures sensitivity;
it does not select parameters or establish approval/eligibility for trading.

## Determinism, errors and limitations

Public models are strict, frozen, extra-forbidden, always revalidated and JSON
round-trip safe. Inputs are materialized once and never mutated. Every public
calculation and descriptive Decimal summary uses a fresh Context(prec=34,
rounding=ROUND_HALF_EVEN), independent of caller precision, rounding, flags or
traps. Reports contain no random split, random identifier, wall-clock time or
network result. Same strategy, bars, features, configs and candidates yield exactly
equal reports and JSON.

Direct invalid model construction raises Pydantic ValidationError. Public operations
raise ResearchValidationInputError for malformed windows/data/configs/calculations,
or ResearchValidationCompatibilityError for unsupported strategy/variant intent
or uncovered feature history, both under ResearchValidationError (ValueError).
There is no silent fallback. Internal reuse of the engine's private validation
helpers is isolated to runner.py; the engine and its execution/evaluation formulas
are unchanged.

Reports audit run boundaries, strategy identity, config and metrics. They do not
persist input datasets or verify external feature numeric provenance; callers retain
those artifacts for replay. There is no calendar/session inference, fitting,
purge/embargo for future labels, regime analysis, optimization, random Monte Carlo,
bootstrap, ML/LLM/agents/MCP, VaR/CVaR, sizing expansion, stop execution,
portfolio aggregation, live/paper trading, API or frontend. Phase 11 is implemented
separately; [risk-engine.md](risk-engine.md) defines its hard limits and causal peak.
The existing orchestration automatically propagates that policy.

Out-of-sample and walk-forward results are evidence about historical stability,
not proof of future profitability. Repeated human selection against a holdout can
still contaminate research; this layer does not prevent selection outside its API.
