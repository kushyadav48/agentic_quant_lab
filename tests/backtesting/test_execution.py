"""Phase 8 exact economics through the real engine, without execution mocks."""
from decimal import Context, Inexact, ROUND_DOWN, localcontext
import socket

import pytest
from pydantic import ValidationError
from quantlab.backtesting import (
    BacktestCompatibilityError, BacktestConfig, BacktestInputError, BacktestResult,
    BacktestSignalConflictError, CostBreakdown, ExecutionCostConfig, SignalAction as A,
)
from quantlab.data import PriceType
from quantlab.strategies import Comparison as C, ConstantOperand, Direction, FeatureOperand
from .helpers import (
    CONFIG, D, INSTRUMENT, MINUTE, bars, group, observations, reference, rule, simulate, strategy,
)


def config(**costs):
    return BacktestConfig(initial_capital=CONFIG.initial_capital, quantity=CONFIG.quantity,
                          execution_costs=ExecutionCostConfig(**costs))


def basis(series, price_type=PriceType.MID):
    return tuple(b.model_copy(update={"price_type": price_type}) for b in series)


def lifecycle(direction):
    return basis(bars((101, 110, 99, 90), (80, 100, 111, 90)) if direction is Direction.LONG
                 else bars((99, 90, 101, 110), (120, 100, 89, 110)))


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_default_zero_cost_preserves_phase7(direction):
    series = lifecycle(direction)
    result = simulate(strategy(direction), series)
    assert result == simulate(strategy(direction), series, config=config())
    assert [s.action for s in result.signals] == ([A.ENTER_LONG, A.EXIT_LONG]
        if direction is Direction.LONG else [A.ENTER_SHORT, A.EXIT_SHORT])
    assert [s.signal_time for s in result.signals] == [series[0].end_time, series[2].end_time]
    assert [f.execution_time for f in result.fills] == [series[1].start_time, series[3].start_time]
    assert [f.execution_price for f in result.fills] == [D("100"), series[3].open]
    assert all(f.reference_price == f.execution_price and f.costs == CostBreakdown()
               and f.costs.total_cost == 0 for f in result.fills)
    trade, = result.closed_trades
    assert trade.reference_gross_pnl == trade.gross_pnl == trade.net_pnl == D("-20")
    assert result.final_equity == D("980")


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
@pytest.mark.parametrize("settings,entry_delta,exit_delta,spread_cost,slippage_cost,commission,fees,net", [
    ({"spread": D("2")}, D("1"), D("1"), D("4"), D("0"), D("0"), D("0"), D("-24")),
    ({"slippage": D("0.5")}, D("0.5"), D("0.5"), D("0"), D("2"), D("0"), D("0"), D("-22")),
    ({"spread": D("2"), "slippage": D("0.5"), "commission_per_unit": D("0.25"),
      "fixed_fee_per_fill": D("1")}, D("1.5"), D("1.5"), D("4"), D("2"), D("1"), D("2"), D("-29")),
])
def test_hand_computed_spread_slippage_and_combined_costs(direction, settings, entry_delta,
        exit_delta, spread_cost, slippage_cost, commission, fees, net):
    series = lifecycle(direction)
    result = simulate(strategy(direction), series, config=config(**settings))
    entry, exit = result.fills
    sign = D("1") if direction is Direction.LONG else D("-1")
    assert entry.reference_price == D("100")
    assert entry.execution_price == D("100") + sign * entry_delta
    assert exit.reference_price == series[3].open
    assert exit.execution_price == series[3].open - sign * exit_delta
    assert entry.costs.spread_cost == exit.costs.spread_cost == spread_cost / 2
    assert entry.costs.slippage_cost == exit.costs.slippage_cost == slippage_cost / 2
    assert entry.costs.commission == exit.costs.commission == commission / 2
    assert entry.costs.fees == exit.costs.fees == fees / 2
    trade, = result.closed_trades
    assert trade.reference_gross_pnl == D("-20")
    assert trade.gross_pnl == D("-20") - spread_cost - slippage_cost
    assert trade.costs == entry.costs.plus(exit.costs)
    assert trade.costs.total_cost == spread_cost + slippage_cost + commission + fees
    assert trade.gross_pnl - commission - fees == trade.net_pnl == net
    assert trade.reference_gross_pnl - trade.costs.total_cost == net
    assert result.realized_pnl == net and result.unrealized_pnl == 0
    assert result.final_equity == D("1000") + net
    assert result.equity_curve[1].realized_pnl == -entry.costs.explicit_cost
    for point in result.equity_curve:
        assert point.equity == CONFIG.initial_capital + point.realized_pnl + point.unrealized_pnl


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
@pytest.mark.parametrize("price_type,buy_spread,sell_spread", [
    (PriceType.MID, D("1"), D("1")),
    (PriceType.BID, D("2"), D("0")),
    (PriceType.ASK, D("0"), D("2")),
])
def test_price_basis_switches_side_without_double_counting(direction, price_type, buy_spread, sell_spread):
    result = simulate(strategy(direction), basis(lifecycle(direction), price_type), config=config(spread=D("2")))
    for fill in result.fills:
        buy = fill.action in (A.ENTER_LONG, A.EXIT_SHORT)
        adjustment = buy_spread if buy else sell_spread
        assert fill.spread_adjustment == adjustment
        assert fill.execution_price == fill.reference_price + (adjustment if buy else -adjustment)
        assert fill.costs.spread_cost == adjustment * CONFIG.quantity
    assert result.closed_trades[0].costs.spread_cost == D("4")
    assert result.closed_trades[0].net_pnl == D("-24")


def test_trade_spread_is_rejected_even_without_fill_and_other_costs_are_supported():
    with pytest.raises(BacktestCompatibilityError, match="TRADE"):
        simulate(series=bars((101,)), config=config(spread=D("1")))
    result = simulate(series=basis(lifecycle(Direction.LONG), PriceType.TRADE),
        config=config(slippage=D("0.5"), commission_per_unit=D("0.25")))
    assert result.closed_trades[0].net_pnl == D("-23")


@pytest.mark.parametrize("quantity", [D("1"), D("2"), D("5"), D("0.3")])
def test_quantity_scaling_and_fixed_fee(quantity):
    instrument = INSTRUMENT.model_copy(update={"quantity_increment": D("0.1")})
    cfg = BacktestConfig(initial_capital=D("1000"), quantity=quantity,
        execution_costs=ExecutionCostConfig(commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1")))
    result = simulate(series=lifecycle(Direction.LONG), instrument=instrument, config=cfg)
    assert all(f.costs.commission == quantity * D("0.25") and f.costs.fees == D("1")
               for f in result.fills)
    assert result.closed_trades[0].net_pnl == D("-10") * quantity - D("0.5") * quantity - D("2")


@pytest.mark.parametrize("direction,closes", [(Direction.LONG, (101, 110)), (Direction.SHORT, (99, 90))])
def test_open_position_entry_costs_are_recognized_without_future_exit_costs(direction, closes):
    result = simulate(strategy(direction, no_exit=True), basis(bars(closes, (101, 100))),
        config=config(spread=D("2"), slippage=D("0.5"), commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1")))
    fill, = result.fills
    assert fill.costs.total_cost == D("4.5")
    assert result.open_position.entry_costs == fill.costs
    assert result.open_position.entry_reference_price == D("100")
    assert result.closed_trades == () and result.realized_pnl == D("-1.5")
    assert result.unrealized_pnl == D("17") and result.final_equity == D("1015.5")
    assert result.equity_curve[0].realized_pnl == 0


def test_final_bar_signal_has_no_fill_or_cost_and_pending_exit_stays_open():
    cfg = config(spread=D("2"), slippage=D("0.5"), commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1"))
    result = simulate(series=basis(bars((101,))), config=cfg)
    assert len(result.signals) == 1 and result.fills == result.closed_trades == ()
    assert result.realized_pnl == result.unrealized_pnl == 0 and result.final_equity == D("1000")
    result = simulate(series=basis(bars((101, 99), (50, 100))), config=cfg)
    assert [s.action for s in result.signals] == [A.ENTER_LONG, A.EXIT_LONG]
    assert len(result.fills) == 1 and result.open_position is not None
    assert result.closed_trades == () and result.realized_pnl == D("-1.5")
    assert result.unrealized_pnl == D("-5") and result.final_equity == D("993.5")


def test_no_same_open_reversal_and_multiple_trade_cost_recognition():
    series = basis(bars((101, 99, 99, 99, 101, 101), (90, 100, 90, 80, 70, 90)))
    result = simulate(strategy(Direction.BOTH), series,
        config=config(spread=D("2"), slippage=D("0.5"), commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1")))
    assert [f.action for f in result.fills] == [A.ENTER_LONG, A.EXIT_LONG, A.ENTER_SHORT, A.EXIT_SHORT]
    assert [f.execution_time for f in result.fills] == [series[i].start_time for i in (1, 2, 3, 5)]
    assert len({f.execution_time for f in result.fills}) == 4
    assert result.realized_pnl == sum(t.net_pnl for t in result.closed_trades) == D("-58")
    assert result.open_position is None and result.final_equity == D("942")


def test_nonzero_costs_preserve_both_direction_conflict():
    spec = strategy(Direction.BOTH, entry=group(rule(C.EQ,
        left=ConstantOperand(value=True), right=ConstantOperand(value=True))))
    with pytest.raises(BacktestSignalConflictError):
        simulate(spec, basis(bars((100,))), config=config(spread=D("2")))


@pytest.mark.parametrize("field", ["spread", "slippage", "commission_per_unit", "fixed_fee_per_fill"])
@pytest.mark.parametrize("value", [D("-1"), D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"), 1, 1.0, True, "1", None])
def test_strict_finite_nonnegative_cost_config(field, value):
    with pytest.raises(ValidationError):
        ExecutionCostConfig(**{field: value})


@pytest.mark.parametrize("field", ["spread_cost", "slippage_cost", "commission", "fees"])
@pytest.mark.parametrize("value", [D("-1"), D("NaN"), D("Infinity"), 1.0])
def test_cost_breakdown_rejects_invalid_components(field, value):
    with pytest.raises(ValidationError):
        CostBreakdown(**{field: value})


def test_unchecked_cost_copies_are_revalidated_at_engine_boundary():
    bad = ExecutionCostConfig().model_copy(update={"slippage": D("-1")})
    with pytest.raises(BacktestInputError):
        simulate(config=CONFIG.model_copy(update={"execution_costs": bad}))
    with pytest.raises(BacktestInputError):
        simulate(config=CONFIG.model_copy(update={"execution_costs": {"spread": 1.0}}))


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_nonpositive_adjusted_execution_price_is_explicit_error(direction):
    with pytest.raises(BacktestInputError, match="nonpositive"):
        simulate(strategy(direction), lifecycle(direction), config=config(slippage=D("200")))


def test_cost_replay_roundtrips_input_immutability_and_network_isolation(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    spec = strategy(features=(reference(period=1),))
    series = lifecycle(Direction.LONG)
    features = observations(spec, series)
    cfg = config(spread=D("2"), slippage=D("0.5"), commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1"))
    inputs = (spec, cfg, cfg.execution_costs, INSTRUMENT, *series, *features)
    before = tuple(x.model_dump_json() for x in inputs)
    result = simulate(spec, series, features, config=cfg)
    assert result == simulate(spec, series, features, config=cfg)
    assert result.model_dump_json() == simulate(spec, series, features, config=cfg).model_dump_json()
    assert tuple(x.model_dump_json() for x in inputs) == before
    assert BacktestResult.model_validate_json(result.model_dump_json()) == result
    for model in (cfg.execution_costs, result.fills[0].costs, *result.fills, *result.closed_trades):
        assert type(model).model_validate_json(model.model_dump_json()) == model
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, next(iter(type(model).model_fields)), None)
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unexpected": D("0")})


def test_ambient_context_does_not_change_execution_models_or_accounting():
    cfg = config(spread=D("0.1234567890123456789"), slippage=D("0.9876543210123456789"),
                 commission_per_unit=D("0.234567890123456789"), fixed_fee_per_fill=D("0.34567890123456789"))
    series = lifecycle(Direction.LONG)
    expected = simulate(series=series, config=cfg)
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact] = True
        actual = simulate(series=series, config=cfg)
        assert actual == expected
        assert BacktestResult.model_validate_json(expected.model_dump_json()) == expected
        assert actual.closed_trades[0].costs.total_cost == expected.closed_trades[0].costs.total_cost
        assert caller.prec == 2 and caller.rounding == ROUND_DOWN and caller.traps[Inexact]


def test_delayed_execution_bar_open_fills_but_delayed_close_cannot_signal():
    series = list(basis(bars((101, 99), (50, 100))))
    series[1] = series[1].model_copy(update={"available_at": series[1].end_time + MINUTE})
    result = simulate(series=series, config=config(spread=D("2"), slippage=D("0.5")))
    assert len(result.signals) == len(result.fills) == 1
    fill, = result.fills
    assert fill.reference_price == D("100") and fill.execution_price == D("101.5")
    assert fill.execution_time == series[1].start_time and result.open_position is not None


def test_execution_does_not_use_fill_bar_high_low_or_close():
    cfg = config(spread=D("2"), slippage=D("0.5"))
    series = lifecycle(Direction.LONG)
    expected = simulate(series=series, config=cfg)
    changed = list(series)
    changed[1] = changed[1].model_copy(update={"high": D("500"), "low": D("1"), "close": D("300")})
    changed[3] = changed[3].model_copy(update={"high": D("900"), "low": D("1"), "close": D("800")})
    actual = simulate(series=changed, config=cfg)
    assert actual.fills == expected.fills
    assert actual.closed_trades == expected.closed_trades
    assert actual.signals[:2] == expected.signals[:2]


def test_future_bars_and_feature_observations_preserve_all_cost_aware_prefixes():
    series = basis(bars((90, 100, 110, 120, 90, 80, 110)))
    spec = strategy(entry=group(rule(C.CROSSES_ABOVE, left=FeatureOperand(feature_id="fast"))),
                    features=(reference(),))
    features = observations(spec, series)
    cfg = config(spread=D("2"), slippage=D("0.5"), commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1"))
    complete = simulate(spec, series, features, config=cfg)
    for length in range(1, len(series) + 1):
        prefix = series[:length]
        causal = tuple(o for o in features if o.timestamp <= prefix[-1].end_time)
        result = simulate(spec, prefix, causal, config=cfg)
        assert result == simulate(spec, prefix, features, config=cfg)
        assert result.signals == tuple(s for s in complete.signals if s.signal_time <= prefix[-1].end_time)
        assert result.fills == tuple(f for f in complete.fills if f.execution_time < prefix[-1].end_time)
        assert result.equity_curve == complete.equity_curve[:length]


@pytest.mark.parametrize("kind,field,value", [
    ("fill", "execution_price", D("99")),
    ("fill", "reference_price", D("99")),
    ("fill", "spread_adjustment", D("3")),
    ("fill", "costs", CostBreakdown(spread_cost=D("9"))),
    ("trade", "gross_pnl", D("9")),
    ("trade", "reference_gross_pnl", D("9")),
    ("trade", "net_pnl", D("9")),
    ("trade", "exit_price", D("99")),
])
def test_public_result_models_reject_inconsistent_accounting(kind, field, value):
    result = simulate(series=lifecycle(Direction.LONG), config=config(spread=D("2")))
    model = result.fills[0] if kind == "fill" else result.closed_trades[0]
    with pytest.raises(ValidationError):
        type(model).model_validate(model.model_copy(update={field: value}))


def test_zero_price_effects_preserve_unrounded_phase7_open():
    price = D("100.123456789012345678901234567890123456789")
    series = bars((101, 110), (50, price))
    for cfg in (config(), config(commission_per_unit=D("0.25"))):
        result = simulate(strategy(no_exit=True), series, config=cfg)
        assert result.fills[0].execution_price == result.fills[0].reference_price == price
        assert result.open_position.entry_price == price


def test_price_effect_below_context_precision_is_rejected_instead_of_silently_ignored():
    with pytest.raises(BacktestInputError, match="precision 34"):
        simulate(series=lifecycle(Direction.LONG), config=config(slippage=D("1e-100")))


def test_unsupported_execution_exponent_overflow_is_input_error():
    with pytest.raises(BacktestInputError, match="precision 34"):
        simulate(series=lifecycle(Direction.LONG), config=config(commission_per_unit=D("1e1000000")))


@pytest.mark.parametrize("field", ["reference_price", "execution_price", "quantity"])
@pytest.mark.parametrize("value", [D("0"), D("-1"), D("NaN"), D("Infinity"), 1.0])
def test_fill_financial_fields_remain_strict_positive_and_finite(field, value):
    fill = simulate(series=lifecycle(Direction.LONG)).fills[0]
    with pytest.raises(ValidationError):
        type(fill).model_validate(fill.model_copy(update={field: value}))


def test_closed_then_open_position_accounting_does_not_charge_entry_twice():
    series = basis(bars((101, 99, 101, 110), (90, 100, 110, 100)))
    result = simulate(series=series,
        config=config(spread=D("2"), slippage=D("0.5"), commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1")))
    trade, = result.closed_trades
    assert trade.reference_gross_pnl == D("20") and trade.net_pnl == D("11")
    assert len(result.fills) == 3 and result.open_position is not None
    assert result.realized_pnl == trade.net_pnl - result.open_position.entry_costs.explicit_cost == D("9.5")
    assert result.unrealized_pnl == D("17") and result.final_equity == D("1026.5")
