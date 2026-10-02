"""Hand-calculated formulas, warm-up, and strict parameter behavior."""
from decimal import Context, localcontext, ROUND_DOWN
import pytest
from quantlab.features import FeatureParameter, FeatureRequest
from .helpers import D, bars, compute, request, values


@pytest.mark.parametrize("name", ["open", "high", "low", "close"])
def test_raw_fields_and_timestamps(name):
    bar = bars((2,))[0].model_copy(update={"open":D(2), "high":D(4), "low":D(1), "close":D(3)})
    observation, = compute((bar,), request(name))
    assert observation.value == getattr(bar, name)
    assert observation.timestamp == bar.end_time
    assert observation.available_at == bar.available_at
    assert observation.input_start == bar.start_time


def test_simple_returns_and_warmup():
    assert values(bars((10, 12, 9)), "simple_return") == (D("0.2"), D("-0.25"))
    assert values(bars((10,)), "simple_return") == ()


def test_log_returns_and_warmup():
    with localcontext(Context(prec=34)):
        expected = (D(2).ln(), D("0.5").ln())
    assert values(bars((1, 2, 1)), "log_return") == expected
    assert values(bars((1,)), "log_return") == ()


@pytest.mark.parametrize("name", ["simple_return", "log_return", "rolling_volatility"])
@pytest.mark.parametrize("price", [D(0), D(-1), D("NaN"), D("Infinity")])
def test_invalid_prices_rejected_even_on_unchecked_copies(name, price):
    series = list(bars())
    series[0] = series[0].model_copy(update={"close":price})
    with pytest.raises(ValueError):
        compute(series, request(name, 2 if name == "rolling_volatility" else None))


def test_sma_and_warmup():
    assert values(bars(), "sma", 3) == (D(2), D(3), D(4))
    assert values(bars((1, 2)), "sma", 3) == ()


def test_ema_sma_seed_and_recursion():
    assert values(bars((1, 2, 3, 8, 10)), "ema", 3) == (D(2), D(5), D("7.5"))
    assert values(bars((1, 2)), "ema", 3) == ()


@pytest.mark.parametrize("name", ["sma", "ema"])
def test_period_one(name):
    assert values(bars(), name, 1) == tuple(D(i) for i in range(1, 6))


def test_rsi_wilder_seed_and_recursion():
    # deltas +2,-1 seed averages 1,0.5; next -1 smooths to 0.5,0.75.
    with localcontext(Context(prec=34)):
        expected = D(100) - D(100) / D(3)
    result = values(bars((1, 3, 2, 1)), "rsi", 2)
    assert result[0] == expected
    assert abs(result[1] - D(40)) < D("1e-30")
    assert values(bars((1, 3)), "rsi", 2) == ()
    assert values(bars((1, 3, 2)), "rsi", 2) == (expected,)


@pytest.mark.parametrize("prices,expected", [((1,2,3,4),100), ((4,3,2,1),0), ((2,2,2,2),50)])
def test_rsi_rising_falling_flat(prices, expected):
    assert values(bars(prices), "rsi", 2) == (D(expected), D(expected))


def test_rsi_period_one():
    assert values(bars((1,2,1,1)), "rsi", 1) == (D(100), D(0), D(50))


def test_population_volatility_and_warmup():
    # returns +0.5,-0.5: mean 0, population variance 0.25 (sample would be 0.5).
    assert values(bars((2,3,"1.5")), "rolling_volatility", 2) == (D("0.5"),)
    assert values(bars((2,3)), "rolling_volatility", 2) == ()
    assert values(bars((2,2,2)), "rolling_volatility", 2) == (D(0),)


@pytest.mark.parametrize("name", ["sma", "ema", "rsi", "rolling_volatility"])
@pytest.mark.parametrize("n", [0, -1, True, 1.0, D(2)])
def test_invalid_parameters(name, n):
    with pytest.raises(ValueError):
        compute(bars(), request(name, n))


def test_volatility_window_one_rejected():
    with pytest.raises(ValueError):
        compute(bars(), request("rolling_volatility",1))


@pytest.mark.parametrize("name", ["sma", "ema", "rsi", "rolling_volatility"])
def test_missing_parameters_rejected(name):
    with pytest.raises(ValueError):
        compute(bars(), request(name))


def test_unknown_extra_and_duplicate_parameters():
    with pytest.raises(ValueError):
        compute(bars(), request("unknown"))
    with pytest.raises(ValueError):
        compute(bars(), request("close", 2))
    with pytest.raises(ValueError):
        compute(bars(), FeatureRequest(feature_id="sma", parameters=(FeatureParameter(name="wrong",value=2),)))


def test_decimal_context_is_isolated():
    requests = (request("sma",3), request("ema",3), request("rsi",2),
                request("rolling_volatility",2), request("log_return"))
    expected = compute(bars(), *requests)
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_DOWN
        assert compute(bars(), *requests) == expected
        assert ctx.prec == 2 and ctx.rounding == ROUND_DOWN


def test_duplicate_parameter_names_rejected():
    with pytest.raises(ValueError):
        FeatureRequest(feature_id="sma", parameters=(FeatureParameter(name="period", value=2),) * 2)
