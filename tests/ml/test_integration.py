"""ML features enter the existing approval, costs, analytics and risk stack."""
import pytest

from quantlab.analytics import analyze_performance
from quantlab.backtesting import BacktestCompatibilityError, BacktestInputError, run_backtest
from quantlab.data import PriceType
from quantlab.features import compute_features, validate_strategy_features
from quantlab.ml import (MLModelConfig, predict_oos, prediction_feature_reference,
                         predictions_to_features, train_model)
from quantlab.risk import RiskAction, RiskConfig
from quantlab.strategies import ConstantOperand, FeatureArgument, FeatureOperand, FeatureReference, FeatureType
from quantlab.validation import HoldoutConfig, ValidationWindow, run_holdout
from tests.backtesting.helpers import D, INSTRUMENT, group, rule, strategy
from tests.risk.test_integration import config, costs
from .helpers import dataset, fixture


def research():
    series, inputs = fixture()
    series = tuple(b.model_copy(update={"price_type": PriceType.MID}) for b in series)
    inputs = tuple(f.model_copy(update={"price_type": PriceType.MID}) for f in inputs)
    model = train_model(dataset(series, inputs))
    predictions = predict_oos(model, dataset(series, inputs, start=4, end=7, target=None))
    features = predictions_to_features(predictions, artifact=model, feature_id="forecast")
    signal = group(rule(left=FeatureOperand(feature_id="forecast"), right=ConstantOperand(value=D(0))))
    spec = strategy(entry=signal, exit=signal, features=(prediction_feature_reference(model, "forecast"),))
    return series, inputs, model, features, spec


def test_backtest_costs_risk_audit_and_analytics_are_preserved():
    series, _, _, features, spec = research()
    result = run_backtest(spec, series[4:], features, instrument=INSTRUMENT, config=config(costs=costs()))
    assert len(result.fills) == 2 and len(result.closed_trades) == 1
    assert result.fills[0].execution_time == series[5].start_time
    assert result.closed_trades[0].costs.total_cost == D(9)
    assert result.closed_trades[0].net_pnl == D(8631)
    assert result.final_equity == D(9631)
    assert result.risk_decisions[0].action is RiskAction.ALLOW
    performance = analyze_performance(result)
    assert performance.trades.closed_trade_count == 1 and performance.trades.net_closed_trade_pnl == D(8631)


def test_ml_signal_rejected_by_hard_risk_limit_before_any_cost():
    series, _, _, features, spec = research()
    result = run_backtest(spec, series[4:], features, instrument=INSTRUMENT,
        config=config(costs=costs(), risk=RiskConfig(max_position_quantity=D(1))))
    assert result.signals and result.risk_decisions
    assert all(d.action is RiskAction.REJECT for d in result.risk_decisions)
    assert result.fills == result.closed_trades == () and result.open_position is None
    assert result.final_equity == D(1000)


def test_approval_remains_mandatory():
    series, _, _, features, spec = research()
    with pytest.raises(BacktestCompatibilityError, match="APPROVED"):
        run_backtest(spec.revise(spec.content), series[4:], features, instrument=INSTRUMENT, config=config())


def test_different_model_cannot_reuse_approved_feature_declaration():
    series, inputs, _, _, spec = research()
    model = train_model(dataset(series, inputs), MLModelConfig(alpha=D(2)))
    predictions = predict_oos(model, dataset(series, inputs, start=4, end=7, target=None))
    features = predictions_to_features(predictions, artifact=model, feature_id="forecast")
    with pytest.raises(BacktestInputError, match="metadata mismatch"):
        run_backtest(spec, series[4:], features, instrument=INSTRUMENT, config=config())
    changed = spec.content.model_copy(update={"features": (prediction_feature_reference(model, "forecast"),)})
    assert changed.content_digest() != spec.content_digest


def test_phase10_windows_and_existing_holdout_runner_accept_oos_predictions():
    series, _, model, features, spec = research()
    split = HoldoutConfig(train=ValidationWindow(start=0, end=4), test=ValidationWindow(start=4, end=7))
    result = run_holdout(spec, series, features, instrument=INSTRUMENT,
                        config=config(costs=costs()), split=split)
    assert result.in_sample.backtest.signals == ()
    assert result.out_of_sample.backtest.closed_trades
    assert all(f.timestamp > model.information_cutoff for f in features)
    assert result.out_of_sample.backtest == run_backtest(spec, series[4:], features,
                                                        instrument=INSTRUMENT, config=config(costs=costs()))


@pytest.mark.parametrize("change", ["unknown", "missing", "negative", "too_large", "boolean", "extra", "indicator"])
def test_ml_declaration_fails_closed(change):
    _, _, model, _, _ = research()
    reference = prediction_feature_reference(model, "forecast")
    if change == "unknown": reference = reference.model_copy(update={"implementation_id": "other"})
    elif change == "indicator": reference = reference.model_copy(update={"feature_type": FeatureType.INDICATOR})
    else:
        arguments = {"missing": (), "negative": (FeatureArgument(name="model_digest", value=-1),),
            "too_large": (FeatureArgument(name="model_digest", value=2**256),),
            "boolean": (FeatureArgument(name="model_digest", value=True),),
            "extra": reference.parameters + (FeatureArgument(name="extra", value=1),)}[change]
        reference = reference.model_copy(update={"parameters": arguments})
    with pytest.raises(ValueError):
        validate_strategy_features(strategy(features=(reference,)))


def test_indicator_pipeline_does_not_synthesize_ml_predictions():
    series, _, _, _, spec = research()
    requests = validate_strategy_features(spec)
    with pytest.raises(ValueError, match="unknown feature"):
        compute_features(series, requests, instrument=INSTRUMENT)
