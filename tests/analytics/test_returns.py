"""Hand-computed ratios, population deviation and explicit Sharpe policy."""
from decimal import Context, Decimal, Inexact, Rounded, ROUND_DOWN, localcontext

import pytest
from quantlab.analytics import AnalyticsConfig, AnalyticsInputError, compute_return_statistics
from .helpers import D, equity


def test_first_mark_is_compared_with_capital_and_exact_population_statistics():
    # Returns 0.1, 0.2: mean 0.15, population variance 0.0025, std 0.05.
    report = compute_return_statistics(D("1000"), equity((1100, 1320)))
    assert tuple(p.period_return for p in report.series) == (D("0.1"), D("0.2"))
    assert report.absolute_return == D("320")
    assert report.cumulative_return == D("0.32")
    assert report.mean_period_return == D("0.15")
    assert report.return_volatility == D("0.05")
    assert report.period_sharpe == D("3")
    assert report.annualized_sharpe is None
    assert report.undefined_period_count == 0


def test_explicit_risk_free_rate_and_annualization():
    cfg = AnalyticsConfig(risk_free_rate_per_period=D("0.05"), annualization_factor=D("4"))
    report = compute_return_statistics(D("1000"), equity((1100, 1320)), cfg)
    assert report.period_sharpe == D("2")
    assert report.annualized_sharpe == D("4")
    assert report.return_volatility == D("0.05")
    cfg = AnalyticsConfig(risk_free_rate_per_period=D("-0.05"))
    assert compute_return_statistics(D("1000"), equity((1100, 1320)), cfg).period_sharpe == D("4")


@pytest.mark.parametrize("values,mean", [((1000,), "0"), ((1000, 1000), "0"), ((1100, 1210), "0.1")])
def test_single_or_constant_returns_have_zero_population_volatility_and_undefined_sharpe(values, mean):
    report = compute_return_statistics(D("1000"), equity(values),
        AnalyticsConfig(annualization_factor=D("252")))
    assert report.mean_period_return == D(mean)
    assert report.return_volatility == 0
    assert report.period_sharpe is report.annualized_sharpe is None


def test_empty_pure_return_series():
    report = compute_return_statistics(D("1000"), ())
    assert report.series == () and report.cumulative_return == report.absolute_return == 0
    assert report.mean_period_return is report.return_volatility is None
    assert report.period_sharpe is report.annualized_sharpe is None


@pytest.mark.parametrize("values,expected", [
    ((0, 100, 200), (D("-1"), None, D("1"))),
    ((-100, -50, 100, 110), (D("-1.1"), None, None, D("0.1"))),
])
def test_nonpositive_previous_equity_keeps_undefined_periods_without_filtering(values, expected):
    report = compute_return_statistics(D("1000"), equity(values))
    assert tuple(p.period_return for p in report.series) == expected
    assert report.undefined_period_count == expected.count(None)
    assert report.mean_period_return is report.return_volatility is None
    assert report.period_sharpe is report.annualized_sharpe is None
    assert report.cumulative_return == D(values[-1]) / D("1000") - 1


def test_final_nonpositive_equity_still_has_defined_return_when_previous_positive():
    report = compute_return_statistics(D("1000"), equity((1100, -110)))
    assert tuple(p.period_return for p in report.series) == (D("0.1"), D("-1.1"))
    assert report.cumulative_return == D("-1.11")
    assert report.return_volatility == D("0.6")


def test_native_decimal_sqrt_and_context_isolation():
    points = equity((1000, 1000, 1300))
    cfg = AnalyticsConfig(annualization_factor=D("2"))
    expected = compute_return_statistics(D("1000"), points, cfg)
    with localcontext(Context(prec=34)):
        assert expected.return_volatility == D("0.02").sqrt()
        assert expected.annualized_sharpe == expected.period_sharpe * D("2").sqrt()
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact] = caller.traps[Rounded] = True
        assert compute_return_statistics(D("1000"), points, cfg) == expected
        assert caller.prec == 2 and caller.traps[Inexact] and caller.traps[Rounded]


@pytest.mark.parametrize("capital", [D("0"), D("-1"), D("NaN"), D("Infinity"), 1000, 1000.0, "1000"])
def test_invalid_initial_capital(capital):
    with pytest.raises(AnalyticsInputError):
        compute_return_statistics(capital, ())


@pytest.mark.parametrize("field,value", [
    ("risk_free_rate_per_period", 0.0), ("risk_free_rate_per_period", D("NaN")),
    ("annualization_factor", D("0")), ("annualization_factor", D("-1")),
])
def test_unchecked_config_revalidated(field, value):
    with pytest.raises(AnalyticsInputError):
        compute_return_statistics(D("1000"), equity((1100,)),
            AnalyticsConfig().model_copy(update={field: value}))


def test_overflow_is_explicit_input_error():
    with pytest.raises(AnalyticsInputError, match="precision 34"):
        compute_return_statistics(D("1e-999999"), equity((D("1e999999"),), D("1e-999999")))
