"""Hand-computed independent windows, causality, costs and strict boundaries."""
from decimal import Context, Inexact, Rounded, ROUND_DOWN, localcontext
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError
from quantlab.analytics import AnalyticsConfig, analyze_performance
from quantlab.backtesting import BacktestConfig, ExecutionCostConfig, run_backtest
from quantlab.data import PriceType
from quantlab.strategies import (
    Comparison, ConstantOperand, Direction, FeatureArgument, FeatureOperand, FeatureReference, FeatureType,
    Parameter, ParameterOperand, ParameterType, StrategyContent,
)
from quantlab.validation import (
    DescriptiveSummary, HoldoutConfig, HoldoutResult, ResearchValidationCompatibilityError,
    ResearchValidationError, ResearchValidationInputError, RobustnessCandidate,
    RobustnessReport, ValidationWindow, WalkForwardConfig, WalkForwardMode,
    WalkForwardReport, holdout_windows, run_holdout, run_parameter_robustness,
    run_walk_forward, walk_forward_folds,
)
from tests.backtesting.helpers import (
    CONFIG, D, INSTRUMENT, MINUTE, START, approve, bars, group, observations,
    reference, rule, strategy,
)


def window(start, end):
    return ValidationWindow(start=start, end=end)


def split(train_end=3, test_end=6, train_start=0, test_start=None):
    return HoldoutConfig(train=window(train_start, train_end),
        test=window(train_end if test_start is None else test_start, test_end))


def hold(series, spec=None, features=(), **kwargs):
    return run_holdout(strategy() if spec is None else spec, series, features,
        instrument=kwargs.pop("instrument", INSTRUMENT), config=kwargs.pop("config", CONFIG),
        split=kwargs.pop("split", split()), **kwargs)


def walk(series, spec=None, features=(), **kwargs):
    return run_walk_forward(strategy() if spec is None else spec, series, features,
        instrument=INSTRUMENT, config=kwargs.pop("config", CONFIG),
        walk_forward=kwargs.pop("walk_forward", WalkForwardConfig(train_size=3, test_size=3, step_size=3)),
        **kwargs)


def variants(values=(100, 105, 110)):
    spec = strategy(entry=group(rule(right=ParameterOperand(name="threshold"))), no_exit=True,
        parameters=(Parameter(name="threshold", type=ParameterType.DECIMAL, default=D(values[0]),
            minimum=D("90"), maximum=D("120")),))
    results = [RobustnessCandidate(candidate_id="baseline", strategy=spec)]
    for index, value in enumerate(values[1:]):
        content = spec.content.model_dump()
        content["parameters"] = (spec.content.parameters[0].model_copy(update={"default":D(value)}),)
        revised = spec.revise(StrategyContent(**content))
        results.append(RobustnessCandidate(candidate_id=f"variant-{index}", strategy=approve(revised)))
    return tuple(results)


def robust(candidates=None, series=None, **kwargs):
    return run_parameter_robustness(variants() if candidates is None else candidates,
        bars((101, 108, 115)) if series is None else series,
        kwargs.pop("features", ()), baseline_candidate_id=kwargs.pop("baseline_candidate_id", "baseline"),
        window=kwargs.pop("window", window(0, 3)), instrument=INSTRUMENT,
        config=kwargs.pop("config", CONFIG), **kwargs)


def test_holdout_exact_indices_order_gaps_and_original_objects():
    series = bars((99, 101, 99, 99, 101, 99, 99, 99))
    cfg = split(3, 7, train_start=1, test_start=4)
    before = tuple(b.model_dump_json() for b in series)
    train, test = holdout_windows(series, cfg, instrument=INSTRUMENT)
    assert train.window == window(1, 3) and test.window == window(4, 7)
    assert train.observation_count == 2 and test.observation_count == 3
    assert (train.first_decision_close, train.last_decision_close) == (series[1].end_time, series[2].end_time)
    assert (test.first_decision_close, test.last_decision_close) == (series[4].end_time, series[6].end_time)
    assert before == tuple(b.model_dump_json() for b in series)
    assert series[1:3][0] is series[1]


def test_holdout_hand_computed_results_and_analytics_reuse():
    series = bars((101, 99, 99, 101, 99, 99), (100, 100, 110, 100, 100, 90))
    spec = strategy()
    cfg = AnalyticsConfig(risk_free_rate_per_period=D("0.001"), annualization_factor=D("252"))
    report = hold(series, spec, analytics_config=cfg)
    for segment, subset in ((report.in_sample, series[:3]), (report.out_of_sample, series[3:])):
        assert segment.backtest == run_backtest(spec, subset, instrument=INSTRUMENT, config=CONFIG)
        assert segment.performance == analyze_performance(segment.backtest, cfg)
        assert segment.backtest.equity_curve[0].equity == D("1000")
    assert report.in_sample.backtest.closed_trades[0].net_pnl == D("20")
    assert report.in_sample.performance.returns.cumulative_return == D("0.02")
    assert report.out_of_sample.backtest.closed_trades[0].net_pnl == D("-20")
    assert report.out_of_sample.performance.returns.cumulative_return == D("-0.02")


def test_oos_starts_flat_and_open_train_position_is_not_closed_or_carried():
    report = hold(bars((101, 110, 120, 90, 90, 90)), strategy(no_exit=True))
    assert report.in_sample.backtest.open_position is not None
    assert report.in_sample.backtest.closed_trades == ()
    assert report.in_sample.performance.has_open_position
    assert report.in_sample.performance.unrealized_pnl == D("20")
    assert report.out_of_sample.backtest.open_position is None
    assert report.out_of_sample.backtest.signals == report.out_of_sample.backtest.fills == ()
    assert report.out_of_sample.backtest.final_equity == CONFIG.initial_capital


def test_final_train_and_test_signals_never_fill_across_boundaries():
    # A suffix exists but is outside the explicit test window.
    report = hold(bars((99, 99, 101, 99, 99, 101, 110)))
    for segment in (report.in_sample, report.out_of_sample):
        assert len(segment.backtest.signals) == 1
        assert segment.backtest.signals[0].signal_time == segment.metadata.last_decision_close
        assert segment.backtest.fills == ()
        assert segment.backtest.open_position is None


def test_pending_final_exit_stays_open_with_unrealized_pnl():
    report = hold(bars((101, 110, 99, 101, 110, 99), (100, 100, 100, 100, 100, 100)))
    for segment in (report.in_sample, report.out_of_sample):
        result = segment.backtest
        assert len(result.signals) == 2 and len(result.fills) == 1
        assert result.open_position is not None and result.closed_trades == ()
        assert result.unrealized_pnl == D("-2")
        assert segment.performance.trades.win_rate is None


@pytest.mark.parametrize("mode, expected", [
    (WalkForwardMode.EXPANDING, ((0, 3, 3, 5), (0, 5, 5, 7), (0, 7, 7, 9))),
    (WalkForwardMode.ROLLING, ((0, 3, 3, 5), (2, 5, 5, 7), (4, 7, 7, 9))),
])
def test_exact_expanding_and_rolling_boundaries(mode, expected):
    folds = walk_forward_folds(bars((99,)*10), WalkForwardConfig(train_size=3,
        test_size=2, step_size=2, mode=mode), instrument=INSTRUMENT)
    assert tuple((f.train.window.start, f.train.window.end, f.test.window.start, f.test.window.end)
                 for f in folds) == expected
    assert tuple(f.fold_index for f in folds) == (0, 1, 2)
    assert all(f.train.last_decision_close < f.test.first_decision_close for f in folds)


@pytest.mark.parametrize("step, starts", [(1, (3,4,5,6,7,8)), (2,(3,5,7)), (4,(3,7)), (20,(3,))])
def test_step_overlap_gaps_and_partial_tail(step, starts):
    cfg = WalkForwardConfig(train_size=3, test_size=2, step_size=step)
    folds = walk_forward_folds(bars((99,)*10), cfg, instrument=INSTRUMENT)
    assert tuple(f.test.window.start for f in folds) == starts


@pytest.mark.parametrize("field", ["train_size", "test_size", "step_size"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3"])
def test_invalid_fold_sizes_strict(field, value):
    values = dict(train_size=3, test_size=2, step_size=2)
    values[field] = value
    with pytest.raises(ValidationError): WalkForwardConfig(**values)


@pytest.mark.parametrize("bounds", [(-1,2), (2,2), (3,2), (0,0), (True,2), (0,2.0)])
def test_invalid_window_bounds(bounds):
    with pytest.raises(ValidationError): window(*bounds)


def test_reversed_or_overlapping_holdout_rejected():
    with pytest.raises(ValidationError): HoldoutConfig(train=window(0,4), test=window(3,6))
    with pytest.raises(ValidationError): HoldoutConfig(train=window(3,6), test=window(0,3))


@pytest.mark.parametrize("series", [(), tuple(reversed(bars())), (bars()[0],bars()[0]), ({},)])
def test_invalid_bars_never_repaired(series):
    with pytest.raises(ResearchValidationError): holdout_windows(series, split(), instrument=INSTRUMENT)


def test_impossible_sizes_and_outside_explicit_window():
    with pytest.raises(ResearchValidationInputError, match="complete fold"):
        walk_forward_folds(bars(), WalkForwardConfig(train_size=3,test_size=2,step_size=1), instrument=INSTRUMENT)
    with pytest.raises(ResearchValidationInputError, match="bar count"): hold(bars())


def test_future_bars_and_features_cannot_change_earlier_fold_or_training():
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),
        features=(reference(period=2),))
    series = bars((101,99,101,110,99,99,101,110,99))
    features = observations(spec, series)
    expected = walk(series, spec, features)
    changed = series[:6]+tuple(b.model_copy(update={"open":D("200"),"close":D("200"),
        "high":D("200"),"low":D("200")}) for b in series[6:])
    actual = walk(changed, spec, observations(spec, changed))
    assert actual.folds[0] == expected.folds[0]
    assert actual.folds[1].in_sample == expected.folds[1].in_sample
    altered_features = tuple(o if o.timestamp <= series[5].end_time else
        o.model_copy(update={"value":D("999")}) for o in features)
    assert walk(series, spec, altered_features).folds[0] == expected.folds[0]
    # Future-only observations can also be appended without affecting a holdout.
    short = hold(series[:6], spec, tuple(o for o in features if o.timestamp <= series[5].end_time))
    extended = hold(series, spec, features)
    assert short == extended
    changed_test = series[:3]+tuple(b.model_copy(update={"open":D("200"),"close":D("200"),
        "high":D("200"),"low":D("200")}) for b in series[3:])
    assert hold(changed_test, spec, observations(spec, changed_test)).in_sample == short.in_sample


@pytest.mark.parametrize("implementation", ["sma", "ema", "rsi"])
def test_explicit_prepared_features_retain_causal_historical_warmup(implementation):
    ref = FeatureReference(feature_id="indicator", implementation_id=implementation,
        feature_type=FeatureType.INDICATOR, parameters=(FeatureArgument(name="period",value=3),))
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="indicator"),
        right=ParameterOperand(name="threshold"))), no_exit=True, features=(ref,),
        parameters=(Parameter(name="threshold",type=ParameterType.DECIMAL,default=D("0")),))
    series = bars((90,100,110,120,130,140))
    prepared = observations(spec, series)
    report = hold(series, spec, prepared)
    assert report.out_of_sample.backtest.signals[0].signal_time == series[3].end_time
    assert report.out_of_sample.backtest.fills[0].execution_time == series[4].start_time
    causal = tuple(o for o in prepared if o.timestamp == series[3].end_time)
    assert len(causal) == 1 and causal[0].input_start < series[3].start_time
    assert report.out_of_sample.backtest == run_backtest(spec, series[3:],
        tuple(o for o in prepared if o.timestamp in {b.end_time for b in series[3:]}),
        instrument=INSTRUMENT,config=CONFIG)
    assert tuple(o for o in observations(spec, series[:4]) if o.timestamp == series[3].end_time) == causal


def test_missing_exact_timestamp_not_forward_filled_or_interpolated():
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))), features=(reference(period=1),))
    series = bars((101,)*6)
    full = observations(spec, series)
    # Omit the first OOS observation. A nearby timestamp also remains unavailable.
    shifted = full[3].model_copy(update={"timestamp":full[3].timestamp+MINUTE/2,
        "available_at":full[3].available_at+MINUTE/2})
    subset = full[:3]+(shifted,)+full[4:]
    result = hold(series, spec, subset).out_of_sample.backtest
    assert result.signals[0].signal_time == series[4].end_time
    assert result.fills[0].execution_time == series[5].start_time


def test_offsets_and_crossing_history_are_explicitly_segment_local():
    series = bars((101,101,99,101,101,101))
    offset = strategy(entry=group(rule(offset=1)), no_exit=True)
    report = hold(series, offset)
    assert report.out_of_sample.backtest.signals[0].signal_time == series[4].end_time
    crossing = strategy(entry=group(rule(Comparison.CROSSES_ABOVE)), no_exit=True)
    assert hold(series,crossing).out_of_sample.backtest.signals == ()


def test_delayed_history_dependencies_cannot_be_hidden_by_segment_slicing(monkeypatch):
    import quantlab.validation.runner as runner

    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))), features=(reference(period=3),))
    series = bars((101,)*6)
    cfg = split(train_end=1, test_start=3, test_end=6)
    # Index 2 is outside both replay windows but contributes to the first OOS SMA.
    delayed = series[:2]+(series[2].model_copy(update={"available_at":START+20*MINUTE}),)+series[3:]
    features = tuple(o for o in observations(spec, series) if o.timestamp >= series[3].end_time)
    first = features[0]
    assert first.timestamp == series[3].end_time
    assert first.input_start <= delayed[2].start_time < first.timestamp
    assert first.available_at < delayed[2].available_at
    # These supplied claims pass sliced OOS validation when the dependency is absent.
    assert run_backtest(spec, delayed[3:6], features, instrument=INSTRUMENT,
        config=CONFIG).signals[0].signal_time == series[3].end_time

    def deny_segment(*args, **kwargs):
        raise AssertionError("segment execution must not begin before history validation")

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_segment", deny_segment)
        with pytest.raises(ResearchValidationInputError, match="contributing bar"):
            hold(delayed, spec, features, split=cfg)
    # Correct propagation gates the first two OOS decisions that depend on index 2.
    delayed_features = tuple(o for o in observations(spec, delayed) if o.timestamp >= series[3].end_time)
    report = hold(delayed, spec, delayed_features, split=cfg)
    assert report.in_sample.backtest.signals == ()
    assert len(report.out_of_sample.backtest.signals) == 1
    assert report.out_of_sample.backtest.signals[0].signal_time == series[5].end_time
    assert report.out_of_sample.backtest.fills == ()


def test_uncovered_feature_history_is_explicit_compatibility_error():
    spec = strategy(features=(reference(period=3),))
    series = bars((101,)*9)
    prepared = observations(spec,series)
    with pytest.raises(ResearchValidationCompatibilityError,match="input_start history"):
        hold(series[3:],spec,tuple(o for o in prepared if o.timestamp > series[2].end_time))


def test_costs_and_analytics_config_preserved_across_all_experiments():
    config = BacktestConfig(initial_capital=D("1000"),quantity=D("2"),
        execution_costs=ExecutionCostConfig(spread=D("2"),slippage=D("0.5"),
            commission_per_unit=D("0.25"),fixed_fee_per_fill=D("1")))
    cfg = AnalyticsConfig(risk_free_rate_per_period=D("0.001"),annualization_factor=D("252"))
    series = tuple(b.model_copy(update={"price_type":PriceType.MID}) for b in
        bars((101,99,99,101,99,99,101,99,99), (100,100,104,100,100,104,100,100,104)))
    holdout = hold(series,config=config,analytics_config=cfg)
    report = walk(series,config=config,analytics_config=cfg)
    candidates = variants()
    robustness = robust(candidates,series,window=window(0,9),config=config,analytics_config=cfg)
    segments = [holdout.in_sample,holdout.out_of_sample]
    segments += [s for f in report.folds for s in (f.in_sample,f.out_of_sample)]
    segments += [c.evaluation for c in robustness.candidates]
    for segment in segments:
        assert segment.backtest.execution_costs == config.execution_costs
        assert segment.backtest.initial_capital == config.initial_capital
        assert segment.performance == analyze_performance(segment.backtest,cfg)
        for fill in segment.backtest.fills:
            assert fill.costs.commission == D("0.5") and fill.costs.fees == D("1")
    assert holdout.in_sample.backtest.closed_trades[0].net_pnl == D("-1")
    assert holdout.out_of_sample.backtest.closed_trades[0].net_pnl == D("-1")


def test_oos_summary_hand_computed_even_and_odd_decimal_median():
    series = bars((99,99,99,101,99,99,101,99,99,101,99,99),
        (100,100,100,100,100,110,100,100,90,100,100,120))
    even = walk(series[:9]).out_of_sample_summary
    assert even.evaluation_count == 2
    assert (even.profitable_count,even.losing_count,even.breakeven_count)==(1,1,0)
    assert even.mean_total_return == even.median_total_return == D("0")
    assert even.minimum_total_return == D("-0.02") and even.maximum_total_return == D("0.02")
    assert even.total_return_range == D("0.04") and even.closed_trade_count == 2
    assert even.mean_maximum_drawdown == D("-0.011")
    odd = walk(series).out_of_sample_summary
    assert odd.evaluation_count == 3 and odd.median_total_return == D("0.02")
    with localcontext(Context(prec=34)):
        assert odd.mean_total_return == D("0.04")/3
    assert odd.closed_trade_count == 3


def test_zero_trade_folds_keep_phase9_undefined_rules():
    report = walk(bars((99,)*9))
    assert report.out_of_sample_summary.breakeven_count == 2
    for fold in report.folds:
        stats = fold.out_of_sample.performance
        assert stats.trades.win_rate is stats.trades.profit_factor is None
        assert stats.returns.period_sharpe is stats.returns.annualized_sharpe is None
        assert stats.returns.cumulative_return == D("0")


def test_open_oos_positions_count_returns_but_no_closed_trades():
    report = walk(bars((99,99,99,101,110,120,101,110,120)),strategy(no_exit=True))
    assert report.out_of_sample_summary.closed_trade_count == 0
    assert report.out_of_sample_summary.profitable_count == 2
    for fold in report.folds:
        assert fold.out_of_sample.performance.has_open_position
        assert fold.out_of_sample.performance.unrealized_pnl == D("20")
        assert fold.out_of_sample.backtest.equity_curve[0].equity == D("1000")


def test_negative_equity_undefined_periods_preserved():
    config=BacktestConfig(initial_capital=D("10"),quantity=D("2"))
    report=hold(bars((99,99,99,101,50,60,70),(100,100,100,100,100,50,60)),
        strategy(no_exit=True),config=config,split=split(3,7))
    stats=report.out_of_sample.performance.returns
    assert stats.undefined_period_count == 2
    assert stats.mean_period_return is stats.return_volatility is stats.period_sharpe is None
    assert stats.cumulative_return == D("-6")


def test_approved_variants_exact_returns_order_and_explicit_baseline_without_ranking():
    candidates = variants()
    report=robust(candidates)
    assert tuple(c.candidate.candidate_id for c in report.candidates)==("baseline","variant-0","variant-1")
    assert tuple(c.evaluation.performance.returns.cumulative_return for c in report.candidates)==(D("0.014"),D("0"),D("0"))
    assert report.candidate_count == 3 and report.baseline_candidate_id == "baseline"
    for result, candidate in zip(report.candidates,candidates):
        assert result.evaluation.backtest.strategy_version == candidate.strategy.version
        assert result.evaluation.backtest.strategy_content_digest == candidate.strategy.content_digest
    assert report.summary.profitable_count == 1
    assert report.summary.median_total_return == D("0")
    reordered=robust(tuple(reversed(candidates)),baseline_candidate_id="variant-0")
    assert reordered.baseline_candidate_id == "variant-0"
    assert tuple(c.candidate.candidate_id for c in reordered.candidates)==("variant-1","variant-0","baseline")
    for model in (RobustnessReport,DescriptiveSummary):
        assert not {"winner","rank","score","recommended_candidate","best"} & model.model_fields.keys()


def test_constant_scalar_type_change_is_not_a_parameter_variant():
    from quantlab.validation.robustness import _structure

    baseline = strategy()
    content = baseline.content.model_dump()
    content["long"]["entry"]["rules"][0]["right"] = ConstantOperand(value=100)
    revised = approve(baseline.revise(StrategyContent(**content)))
    assert type(baseline.content.long.entry.rules[0].right.value) is D
    assert type(revised.content.long.entry.rules[0].right.value) is int
    assert baseline.content_digest != revised.content_digest
    candidates = (RobustnessCandidate(candidate_id="baseline", strategy=baseline),
        RobustnessCandidate(candidate_id="integer", strategy=revised))
    before = tuple(c.model_dump_json() for c in candidates)
    with pytest.raises(ResearchValidationCompatibilityError, match="structure"):
        robust(candidates)
    assert _structure(baseline) != _structure(revised)
    assert tuple(c.model_dump_json() for c in candidates) == before


@pytest.mark.parametrize("numeric", [1, D("1")], ids=["integer", "decimal"])
def test_boolean_and_numeric_constants_are_structurally_distinct(numeric):
    baseline = strategy(entry=group(rule(Comparison.EQ,
        left=ConstantOperand(value=numeric), right=ConstantOperand(value=numeric))))
    content = baseline.content.model_dump()
    content["long"]["entry"] = group(rule(Comparison.EQ,
        left=ConstantOperand(value=True), right=ConstantOperand(value=True)))
    revised = approve(baseline.revise(StrategyContent(**content)))
    assert baseline.content_digest != revised.content_digest
    with pytest.raises(ResearchValidationCompatibilityError, match="structure"):
        robust((RobustnessCandidate(candidate_id="baseline", strategy=baseline),
            RobustnessCandidate(candidate_id="boolean", strategy=revised)))


@pytest.mark.parametrize("change", ["entry", "exit", "direction", "implementation",
    "feature_argument", "feature_argument_type", "parameter_type", "minimum", "maximum", "feature_alias"])
def test_approved_non_default_structure_changes_rejected(change):
    baseline = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"),
        right=ParameterOperand(name="threshold"))), features=(reference(),),
        parameters=variants()[0].strategy.content.parameters)
    content = baseline.content.model_dump()
    if change in ("entry", "exit"):
        content["long"][change]["rules"][0]["comparison"] = Comparison.GE
    elif change == "direction":
        content.update(direction=Direction.SHORT, short=content["long"], long=None)
    elif change == "implementation":
        content["features"][0]["implementation_id"] = "ema"
    elif change in ("feature_argument", "feature_argument_type"):
        content["features"][0]["parameters"][0]["value"] = 3 if change == "feature_argument" else D("2")
    elif change == "parameter_type":
        content["parameters"][0].update(type=ParameterType.INTEGER, default=100,
            minimum=90, maximum=120)
    elif change in ("minimum", "maximum"):
        content["parameters"][0][change] = D("95" if change == "minimum" else "115")
    else:
        content["features"][0]["feature_id"] = "renamed"
        content["long"]["entry"]["rules"][0]["left"]["feature_id"] = "renamed"
    revised = approve(baseline.revise(StrategyContent(**content)))
    assert baseline.content_digest != revised.content_digest
    series = bars((101, 108, 115))
    features = observations(baseline, series)
    # The baseline is executable; each changed candidate is independently approved.
    assert robust((RobustnessCandidate(candidate_id="baseline", strategy=baseline),),
        series, features=features).candidate_count == 1
    with pytest.raises(ResearchValidationCompatibilityError, match="structure"):
        robust((RobustnessCandidate(candidate_id="baseline", strategy=baseline),
            RobustnessCandidate(candidate_id="changed", strategy=revised)), series, features=features)


@pytest.mark.parametrize("kind", ["draft", "validated", "revision", "stale_approval"])
def test_approval_integrity_holdout_walk_and_robustness(kind):
    approved = strategy()
    content=approved.content.model_dump()
    content["name"]="Changed"
    revised=approved.revise(StrategyContent(**content))
    bad = {"draft":strategy(approved=False), "validated":strategy(approved=False).mark_validated(),
        "revision":revised, "stale_approval":approved.model_copy(update={"content":revised.content})}[kind]
    for operation in (lambda:hold(bars((99,)*6),bad), lambda:walk(bars((99,)*6),bad),
        lambda:robust((RobustnessCandidate.model_construct(candidate_id="baseline",strategy=bad),))):
        with pytest.raises(ResearchValidationError): operation()


@pytest.mark.parametrize("default", [True, 100, D("89"), D("121"), D("NaN")])
def test_variant_parameter_type_and_bounds_revalidated(default):
    candidate=variants()[0]
    parameter=candidate.strategy.content.parameters[0].model_copy(update={"default":default})
    badcontent=candidate.strategy.content.model_copy(update={"parameters":(parameter,)})
    bad=candidate.model_copy(update={"strategy":candidate.strategy.model_copy(update={"content":badcontent})})
    with pytest.raises(ResearchValidationError): robust((bad,))


def test_undeclared_parameter_and_feature_argument_changes_cannot_be_variants():
    baseline=variants()[0]
    content=baseline.strategy.content.model_dump()
    content["parameters"] += (Parameter(name="extra",type=ParameterType.INTEGER,default=1),)
    changed=approve(baseline.strategy.revise(StrategyContent(**content)))
    with pytest.raises(ResearchValidationCompatibilityError):
        robust((baseline,RobustnessCandidate(candidate_id="extra",strategy=changed)))
    with pytest.raises(ResearchValidationCompatibilityError):
        robust((baseline,RobustnessCandidate(candidate_id="other",strategy=strategy(features=(reference(),)))))


@pytest.mark.parametrize("kind", ["empty", "duplicate", "missing_baseline", "non_string_baseline"])
def test_candidate_collection_validation(kind):
    candidates=variants()
    kwargs={}
    if kind=="empty": candidates=()
    elif kind=="duplicate": candidates=(candidates[0],candidates[0])
    elif kind=="missing_baseline": kwargs["baseline_candidate_id"]="missing"
    else: kwargs["baseline_candidate_id"]=True
    with pytest.raises(ResearchValidationInputError): robust(candidates,**kwargs)


def test_repeatability_json_all_public_models_immutability_and_network_isolation(monkeypatch):
    def deny(*args,**kwargs): raise AssertionError("network forbidden")
    series=list(bars((101,99,99,101,99,99,101,99,99)))
    spec=strategy()
    candidates=list(variants())
    cfg=AnalyticsConfig(annualization_factor=D("252"))
    splitcfg=split()
    walkcfg=WalkForwardConfig(train_size=3,test_size=3,step_size=3)
    inputs=(spec,CONFIG,INSTRUMENT,cfg,splitcfg,walkcfg,*series,*candidates)
    before=tuple(m.model_dump_json() for m in inputs)
    monkeypatch.setattr(socket,"socket",deny)
    monkeypatch.setattr(socket,"create_connection",deny)
    h=hold(series,spec,split=splitcfg,analytics_config=cfg)
    w=walk(series,spec,walk_forward=walkcfg,analytics_config=cfg)
    r=robust(candidates,series,analytics_config=cfg)
    assert h==hold(series,spec,split=splitcfg,analytics_config=cfg)
    assert w==walk(series,spec,walk_forward=walkcfg,analytics_config=cfg)
    assert r==robust(candidates,series,analytics_config=cfg)
    assert before==tuple(m.model_dump_json() for m in inputs)
    models=(splitcfg,splitcfg.train,walkcfg,h,h.in_sample,h.in_sample.metadata,w,
        *w.folds,*(f.fold for f in w.folds),w.out_of_sample_summary,r,*r.candidates,*candidates)
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json())==model
        with pytest.raises(ValidationError,match="frozen"):
            setattr(model,next(iter(type(model).model_fields)),None)
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(),"extra":None})


def test_hostile_decimal_context_and_traps_do_not_change_results_or_caller():
    series=bars((101,99,99,101,99,99,101,99,99),(100,100,110,100,100,90,100,100,120))
    expected=(hold(series),walk(series),robust(series=series))
    with localcontext(Context(prec=2,rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact]=caller.traps[Rounded]=True
        assert (hold(series),walk(series),robust(series=series))==expected
        for model in expected:
            assert type(model).model_validate_json(model.model_dump_json())==model
        assert caller.prec==2 and caller.rounding==ROUND_DOWN
        assert caller.traps[Inexact] and caller.traps[Rounded]


@pytest.mark.parametrize("kind", ["split", "walk", "cost", "analytics", "bar", "feature", "instrument"])
def test_malformed_unchecked_nested_inputs_revalidated(kind):
    spec=strategy(features=(reference(period=1),))
    series=bars((101,)*6)
    prepared=observations(spec,series)
    kwargs={}
    if kind=="split": kwargs["split"]=split().model_copy(update={"test":window(3,6).model_copy(update={"start":0})})
    elif kind=="walk":
        bad=WalkForwardConfig(train_size=3,test_size=3,step_size=3).model_copy(update={"step_size":True})
        with pytest.raises(ResearchValidationInputError): walk(series,spec,prepared,walk_forward=bad)
        return
    elif kind=="cost": kwargs["config"]=CONFIG.model_copy(update={"execution_costs":ExecutionCostConfig().model_copy(update={"spread":D("-1")})})
    elif kind=="analytics": kwargs["analytics_config"]=AnalyticsConfig().model_copy(update={"annualization_factor":D("0")})
    elif kind=="bar": series=(series[0].model_copy(update={"open":1.0}),)+series[1:]
    elif kind=="feature": prepared=(prepared[0].model_copy(update={"available_at":START}),)+prepared[1:]
    else: kwargs["instrument"]=INSTRUMENT.model_copy(update={"tick_size":D("0")})
    with pytest.raises(ResearchValidationError): hold(series,spec,prepared,**kwargs)


def test_fresh_import_offline():
    code="import socket; deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); socket.socket=deny; socket.create_connection=deny; import quantlab.validation"
    subprocess.run([sys.executable,"-c",code],check=True,capture_output=True,text=True)


@pytest.mark.parametrize("kind", ["duplicate", "unsorted", "undeclared", "metadata"])
def test_full_feature_collection_validation_before_slicing(kind):
    spec=strategy(features=(reference(period=1),))
    series=bars((101,)*6)
    prepared=observations(spec,series)
    if kind=="duplicate": prepared=prepared+(prepared[-1],)
    elif kind=="unsorted": prepared=tuple(reversed(prepared))
    elif kind=="undeclared": prepared=prepared[:-1]+(prepared[-1].model_copy(update={"feature_id":"unknown"}),)
    else: prepared=prepared[:-1]+(prepared[-1].model_copy(update={"implementation_id":"ema"}),)
    with pytest.raises(ResearchValidationInputError): hold(series,spec,prepared)


def test_supplied_features_and_lists_preserved_across_replay():
    series=list(bars((101,)*9))
    spec=strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),features=(reference(period=2),))
    prepared=list(observations(spec,series))
    original=tuple(o.model_dump_json() for o in prepared)
    original_bars=tuple(b.model_dump_json() for b in series)
    h=hold(series,spec,prepared)
    w=walk(series,spec,prepared)
    assert h==hold(iter(series),spec,iter(prepared))
    assert w==walk(iter(series),spec,iter(prepared))
    assert original==tuple(o.model_dump_json() for o in prepared)
    assert original_bars==tuple(b.model_dump_json() for b in series)


def test_delayed_exact_feature_remains_unavailable_in_oos():
    series=bars((101,)*6)
    spec=strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),features=(reference(period=1),))
    prepared=observations(spec,series)
    delayed=tuple(o.model_copy(update={"available_at":START+100*MINUTE}) for o in prepared)
    assert hold(series,spec,delayed).out_of_sample.backtest.signals==()


def test_one_bar_oos_segment_is_reported_without_execution():
    result=hold(bars((101,)*4),split=split(3,4))
    assert result.out_of_sample.metadata.observation_count==1
    assert len(result.out_of_sample.backtest.signals)==1
    assert result.out_of_sample.backtest.fills==()
    assert result.out_of_sample.performance.trades.closed_trade_count==0


def test_robustness_can_use_historical_context_and_subset_window():
    candidates=variants()
    series=bars((101,108,115,101,108,115))
    result=robust(candidates,series,window=window(3,6))
    assert result.metadata.window==window(3,6)
    for entry,candidate in zip(result.candidates,candidates):
        assert entry.evaluation.backtest==run_backtest(candidate.strategy,series[3:],instrument=INSTRUMENT,config=CONFIG)


def test_walk_mode_is_strict_and_not_silently_defaulted():
    with pytest.raises(ValidationError):
        WalkForwardConfig(train_size=3,test_size=3,step_size=3,mode="expanding")
    malformed=WalkForwardConfig(train_size=3,test_size=3,step_size=3).model_copy(update={"mode":"invalid"})
    with pytest.raises(ResearchValidationInputError): walk(bars((99,)*6),walk_forward=malformed)


def test_unsupported_strategy_intent_and_cost_basis_still_fail_closed():
    from quantlab.strategies import DistanceUnit, FixedDistance
    spec=strategy(stop_loss=FixedDistance(value=D("1"),unit=DistanceUnit.PRICE))
    with pytest.raises(ResearchValidationCompatibilityError): hold(bars((99,)*6),spec)
    config=CONFIG.model_copy(update={"execution_costs":ExecutionCostConfig(spread=D("1"))})
    with pytest.raises(ResearchValidationCompatibilityError): hold(bars((99,)*6),config=config)


def test_quantity_increment_remains_enforced_on_every_segment():
    config=BacktestConfig(initial_capital=D("1000"),quantity=D("1.5"))
    with pytest.raises(ResearchValidationInputError,match="quantity_increment"):
        hold(bars((99,)*6),config=config)
