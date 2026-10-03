"""Orchestrate independent existing backtests and analytics, without fitting."""
from collections.abc import Iterable
from decimal import DecimalException, localcontext

from quantlab._decimal import deterministic_context
from quantlab.analytics import AnalyticsConfig, analyze_performance
from quantlab.backtesting import BacktestConfig, run_backtest
from quantlab.backtesting.engine import _validate_inputs, _validate_strategy
from quantlab.backtesting.errors import BacktestCompatibilityError
from quantlab.data import Instrument, MarketBar
from quantlab.features import FeatureObservation
from quantlab.strategies import StrategySpecification
from .errors import ResearchValidationCompatibilityError, ResearchValidationError, ResearchValidationInputError
from .models import HoldoutConfig, HoldoutResult, SegmentResult, WalkForwardConfig, WalkForwardFoldResult, WalkForwardReport, WindowMetadata
from .splits import holdout_windows, walk_forward_folds
from ._summary import summarize


def _prepare(strategy, bars, features, instrument, config, analytics_config):
    """Reuse engine contract checks on full history before segment selection.

    In particular, delayed historical bars must not be hidden by slicing before
    feature-dependency availability validation. No history bar is replayed here.
    """
    instrument = Instrument.model_validate(instrument)
    config = BacktestConfig.model_validate(config)
    analytics_config = AnalyticsConfig() if analytics_config is None else AnalyticsConfig.model_validate(analytics_config)
    strategy, requests = _validate_strategy(strategy, instrument)
    records, observations = _validate_inputs(bars, features, strategy, instrument, requests)
    for observation in observations:
        if observation.input_start < records[0].start_time:
            raise ResearchValidationCompatibilityError("supplied bars do not cover feature input_start history")
    return strategy, records, observations, instrument, config, analytics_config


def _segment(strategy, records, observations, instrument, config, analytics_config,
             metadata: WindowMetadata) -> SegmentResult:
    window = metadata.window
    segment = records[window.start:window.end]
    closes = {b.end_time for b in segment}
    features = tuple(o for o in observations if o.timestamp in closes)
    result = run_backtest(strategy, segment, features, instrument=instrument, config=config)
    return SegmentResult(metadata=metadata, backtest=result,
        performance=analyze_performance(result, analytics_config))


def _raise_boundary(exc):
    if isinstance(exc, ResearchValidationError):
        raise exc
    if isinstance(exc, BacktestCompatibilityError):
        raise ResearchValidationCompatibilityError(str(exc)) from exc
    raise ResearchValidationInputError(f"invalid research validation input/calculation: {exc}") from exc


def run_holdout(strategy: StrategySpecification, bars: Iterable[MarketBar],
                features: Iterable[FeatureObservation] = (), *, instrument: Instrument,
                config: BacktestConfig, split: HoldoutConfig,
                analytics_config: AnalyticsConfig | None = None) -> HoldoutResult:
    """Same approved specification on separate in-sample/OOS runs, both flat.

    Supplied features may use pre-segment causal history. Offsets/crossings are
    segment-local; missing early operands stay unavailable. No feature fitting,
    preparation, position carry, forced close or outside-segment fill occurs.
    """
    with localcontext(deterministic_context()):
        try:
            strategy, records, observations, instrument, config, analytics_config = _prepare(
                strategy, bars, features, instrument, config, analytics_config)
            split = HoldoutConfig.model_validate(split)
            train, test = holdout_windows(records, split, instrument=instrument)
            return HoldoutResult(config=split, backtest_config=config, analytics_config=analytics_config,
                in_sample=_segment(strategy, records, observations, instrument, config, analytics_config, train),
                out_of_sample=_segment(strategy, records, observations, instrument, config, analytics_config, test))
        except (ValueError, TypeError, DecimalException) as exc:
            _raise_boundary(exc)


def run_walk_forward(strategy: StrategySpecification, bars: Iterable[MarketBar],
                     features: Iterable[FeatureObservation] = (), *, instrument: Instrument,
                     config: BacktestConfig, walk_forward: WalkForwardConfig,
                     analytics_config: AnalyticsConfig | None = None) -> WalkForwardReport:
    """Evaluate fixed approved intent over independent complete sequential folds."""
    with localcontext(deterministic_context()):
        try:
            strategy, records, observations, instrument, config, analytics_config = _prepare(
                strategy, bars, features, instrument, config, analytics_config)
            walk_forward = WalkForwardConfig.model_validate(walk_forward)
            planned = walk_forward_folds(records, walk_forward, instrument=instrument)
            folds = tuple(WalkForwardFoldResult(fold=fold,
                in_sample=_segment(strategy, records, observations, instrument, config, analytics_config, fold.train),
                out_of_sample=_segment(strategy, records, observations, instrument, config, analytics_config, fold.test))
                for fold in planned)
            return WalkForwardReport(config=walk_forward, backtest_config=config,
                analytics_config=analytics_config, folds=folds,
                out_of_sample_summary=summarize(tuple(f.out_of_sample.performance for f in folds)))
        except (ValueError, TypeError, DecimalException) as exc:
            _raise_boundary(exc)
