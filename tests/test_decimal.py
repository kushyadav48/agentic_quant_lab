"""Default template mutations cannot affect project arithmetic or its contracts."""
from decimal import (
    DefaultContext, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow,
    ROUND_DOWN, localcontext,
)

import pytest
from quantlab._decimal import deterministic_context
from quantlab.backtesting import BacktestConfig, ExecutionCostConfig
from quantlab.ml import predict_oos, train_model
from quantlab.risk import RiskConfig
from quantlab.strategies import FeatureArgument, FeatureOperand, FeatureReference, FeatureType
from quantlab.validation import WalkForwardConfig, run_walk_forward
from tests.backtesting.helpers import D, INSTRUMENT, bars, group, observations, rule, simulate, strategy
from tests.ml.helpers import dataset


@pytest.mark.parametrize("hostile_traps", [False, True])
def test_ml_values_independent_of_default_context_and_traps(hostile_traps):
    saved = DefaultContext.copy()
    with localcontext() as caller:
        caller.clear_flags()
        before = (str(caller), caller.traps.copy(), caller.flags.copy())
        try:
            expected_data = dataset()
            expected_model = train_model(expected_data)
            expected_oos = dataset(start=4, end=7, target=None)
            expected_predictions = predict_oos(expected_model, expected_oos)
            DefaultContext.prec = 2
            DefaultContext.rounding = ROUND_DOWN
            DefaultContext.Emin = -2
            DefaultContext.Emax = 2
            DefaultContext.clamp = 1
            DefaultContext.capitals = 0
            for signal in DefaultContext.traps:
                DefaultContext.traps[signal] = hostile_traps
                DefaultContext.flags[signal] = True
            actual_data = dataset()
            actual_model = train_model(actual_data)
            actual_oos = dataset(start=4, end=7, target=None)
            assert actual_data == expected_data
            assert actual_model.coefficients == expected_model.coefficients
            assert actual_model.intercept == expected_model.intercept
            assert actual_model == expected_model
            assert actual_oos == expected_oos
            assert predict_oos(actual_model, actual_oos) == expected_predictions
            # Undefined arithmetic still fails, while ordinary rounding is permitted.
            with localcontext(deterministic_context()) as arithmetic:
                assert Decimal(1) / Decimal(3) > 0
                assert arithmetic.flags[Inexact]
                with pytest.raises(DivisionByZero):
                    Decimal(1) / Decimal(0)
                with pytest.raises(InvalidOperation):
                    Decimal(0) / Decimal(0)
                with pytest.raises(Overflow):
                    Decimal("1e999999") * Decimal(10)
            assert (str(caller), caller.traps.copy(), caller.flags.copy()) == before
        finally:
            for field in ("prec", "rounding", "Emin", "Emax", "clamp", "capitals"):
                setattr(DefaultContext, field, getattr(saved, field))
            DefaultContext.traps.update(saved.traps)
            DefaultContext.flags.update(saved.flags)


@pytest.mark.parametrize("hostile_traps", [False, True])
def test_feature_backtest_analytics_validation_values_independent_of_default_context(hostile_traps):
    declarations = tuple(FeatureReference(feature_id=name, implementation_id=name,
        feature_type=FeatureType.INDICATOR, parameters=() if parameter is None else
        (FeatureArgument(name=parameter, value=2),)) for name, parameter in (
            ("open", None), ("high", None), ("low", None), ("close", None),
            ("simple_return", None), ("log_return", None), ("sma", "period"),
            ("ema", "period"), ("rsi", "period"), ("rolling_volatility", "window")))
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="sma"))),
        features=declarations, no_exit=True)
    series = bars((101, 110, 99, 90, 120, 130, 80, 100, 140))
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(commission_per_unit=D("0.001")),
        risk=RiskConfig(max_notional_exposure=D("1000")))
    saved = DefaultContext.copy()
    with localcontext() as caller:
        caller.clear_flags()
        before = (str(caller), caller.traps.copy(), caller.flags.copy())
        try:
            expected_features = observations(spec, series)
            expected_backtest = simulate(spec, series, expected_features, config=config)
            windows = WalkForwardConfig(train_size=3, test_size=3, step_size=3)
            expected_report = run_walk_forward(spec, series, expected_features,
                instrument=INSTRUMENT, config=config, walk_forward=windows)
            assert expected_backtest.fills and expected_backtest.risk_decisions
            DefaultContext.prec = 2
            DefaultContext.rounding = ROUND_DOWN
            DefaultContext.Emin = -2
            DefaultContext.Emax = 2
            DefaultContext.clamp = 1
            DefaultContext.capitals = 0
            for signal in DefaultContext.traps:
                DefaultContext.traps[signal] = hostile_traps
                DefaultContext.flags[signal] = True
            actual_features = observations(spec, series)
            assert tuple(o.value for o in actual_features) == tuple(o.value for o in expected_features)
            assert actual_features == expected_features
            assert simulate(spec, series, actual_features, config=config) == expected_backtest
            assert run_walk_forward(spec, series, actual_features, instrument=INSTRUMENT,
                config=config, walk_forward=windows) == expected_report
            assert (str(caller), caller.traps.copy(), caller.flags.copy()) == before
        finally:
            for field in ("prec", "rounding", "Emin", "Emax", "clamp", "capitals"):
                setattr(DefaultContext, field, getattr(saved, field))
            DefaultContext.traps.update(saved.traps)
            DefaultContext.flags.update(saved.flags)
