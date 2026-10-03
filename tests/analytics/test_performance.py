"""Real-engine net economics, strict reports, replay and malformed copies."""
from datetime import timedelta
from decimal import Context, Inexact, Rounded, ROUND_DOWN, localcontext
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError
from quantlab.analytics import (
    AnalyticsConfig, AnalyticsError, AnalyticsInputError, PerformanceReport, analyze_performance,
)
from quantlab.backtesting import BacktestConfig, ExecutionCostConfig
from quantlab.data import PriceType, Timeframe
from quantlab.strategies import Direction
from tests.backtesting.helpers import CONFIG, D, MINUTE, START, bars, simulate, strategy
from .helpers import four_trades


def costs():
    return BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(spread=D("2"), slippage=D("0.5"),
            commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1")))


def mid(series):
    return tuple(b.model_copy(update={"price_type": PriceType.MID}) for b in series)


def test_zero_trade_backtest():
    result = simulate(series=bars((99, 99, 99)))
    report = analyze_performance(result)
    stats = report.trades
    assert stats.closed_trade_count == stats.winning_trade_count == stats.losing_trade_count == stats.breakeven_trade_count == 0
    assert stats.win_rate is stats.loss_rate is stats.average_trade is None
    assert stats.average_winner is stats.average_loser is stats.largest_winner is stats.largest_loser is None
    assert stats.profit_factor is None
    assert stats.average_holding_duration is stats.minimum_holding_duration is stats.maximum_holding_duration is None
    assert stats.gross_profit == stats.gross_loss == stats.net_closed_trade_pnl == 0
    assert report.returns.absolute_return == report.returns.cumulative_return == 0
    assert report.has_open_position is False
    assert report.config == AnalyticsConfig()


def test_exact_trade_metrics_classification_and_elapsed_holding_times():
    result = four_trades()
    assert tuple(t.net_pnl for t in result.closed_trades) == tuple(map(D, (200, 100, -100, 0)))
    report = analyze_performance(result)
    stats = report.trades
    assert (stats.closed_trade_count, stats.winning_trade_count, stats.losing_trade_count, stats.breakeven_trade_count) == (4, 2, 1, 1)
    assert stats.win_rate == D("0.5") and stats.loss_rate == D("0.25")
    assert stats.gross_profit == D("300") and stats.gross_loss == D("100")
    assert stats.net_closed_trade_pnl == D("200") and stats.average_trade == D("50")
    assert stats.average_winner == D("150") and stats.average_loser == D("-100")
    assert stats.largest_winner == D("200") and stats.largest_loser == D("-100")
    assert stats.profit_factor == D("3")
    assert stats.minimum_holding_duration == MINUTE
    assert stats.maximum_holding_duration == 4 * MINUTE
    assert stats.average_holding_duration == timedelta(seconds=150)
    assert report.returns.absolute_return == D("200") and report.returns.cumulative_return == D("0.2")
    assert report.final_equity == D("1200")
    assert report.strategy_content_digest == result.strategy_content_digest
    assert report.timeframe is result.timeframe and report.price_type is result.price_type


@pytest.mark.parametrize("exit_price,win,loss,even,factor", [(110, 1, 0, 0, None), (90, 0, 1, 0, D("0")), (100, 0, 0, 1, None)])
def test_all_winners_all_losers_all_breakeven_undefined_rules(exit_price, win, loss, even, factor):
    result = simulate(series=bars((101, 99, 99), (101, 100, exit_price)))
    stats = analyze_performance(result).trades
    assert (stats.winning_trade_count, stats.losing_trade_count, stats.breakeven_trade_count) == (win, loss, even)
    assert stats.profit_factor == factor
    if not win: assert stats.average_winner is stats.largest_winner is None
    if not loss: assert stats.average_loser is stats.largest_loser is None
    if even:
        assert stats.average_trade == stats.win_rate == stats.loss_rate == 0


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_phase8_all_costs_use_net_pnl_even_when_execution_gross_is_positive(direction):
    long = direction is Direction.LONG
    series = mid(bars((101, 99, 99) if long else (99, 101, 101), (100, 100, 104 if long else 96)))
    result = simulate(strategy(direction), series, config=costs())
    trade, = result.closed_trades
    assert trade.reference_gross_pnl == D("8") and trade.gross_pnl == D("2")
    assert trade.net_pnl == D("-1")
    stats = analyze_performance(result).trades
    assert stats.winning_trade_count == 0 and stats.losing_trade_count == 1
    assert stats.net_closed_trade_pnl == stats.average_loser == D("-1")
    assert stats.gross_profit == 0 and stats.gross_loss == D("1")


def test_closed_then_open_accounting_uses_authoritative_final_equity():
    result = simulate(series=mid(bars((101, 99, 101, 110), (90, 100, 110, 100))), config=costs())
    before = result.model_dump_json()
    report = analyze_performance(result)
    assert report.trades.closed_trade_count == 1
    assert report.trades.net_closed_trade_pnl == D("11")
    assert report.realized_pnl == D("9.5")  # open entry has paid another 1.5
    assert report.unrealized_pnl == D("17") and report.final_equity == D("1026.5")
    assert report.returns.absolute_return == D("26.5")
    assert report.returns.cumulative_return == D("0.0265")
    assert report.has_open_position is True
    assert result.model_dump_json() == before and len(result.closed_trades) == 1 and len(result.fills) == 3


def test_only_open_position_still_has_account_return_with_undefined_trade_ratios():
    result = simulate(strategy(no_exit=True), mid(bars((101, 110), (101, 100))), config=costs())
    report = analyze_performance(result)
    assert report.trades.closed_trade_count == 0 and report.trades.win_rate is None
    assert report.realized_pnl == D("-1.5") and report.unrealized_pnl == D("17")
    assert report.returns.absolute_return == D("15.5")
    assert report.returns.cumulative_return == D("0.0155")
    assert report.has_open_position


def test_initial_mark_is_real_engine_zero_return_and_negative_equity_is_permitted():
    cfg = BacktestConfig(initial_capital=D("10"), quantity=D("2"))
    result = simulate(strategy(no_exit=True), bars((101, 50, 60), (100, 100, 50)), config=cfg)
    report = analyze_performance(result)
    assert report.returns.series[0].period_return == 0
    assert report.final_equity == D("-70")
    assert report.returns.cumulative_return == D("-8")
    assert report.returns.undefined_period_count == 1
    assert report.returns.mean_period_return is None
    assert report.drawdown.maximum_drawdown_percentage == D("-10")


def test_holding_duration_integer_microsecond_mean_uses_half_even():
    # Two real closed trades with tiny, gapped bars: mean durations 1.5us -> 2us.
    series = bars((101, 99, 101, 99, 99), (100, 100, 110, 100, 110))
    offsets = (0, 1, 2, 3, 5)
    tiny = timedelta(microseconds=1)
    series = tuple(b.model_copy(update={"start_time": START + i * tiny,
        "end_time": START + (i + 1) * tiny,
        "available_at": START + (i + 1) * tiny}) for b, i in zip(series, offsets))
    stats = analyze_performance(simulate(series=series)).trades
    assert stats.minimum_holding_duration == tiny and stats.maximum_holding_duration == 2 * tiny
    assert stats.average_holding_duration == 2 * tiny


def test_deterministic_replay_round_trip_immutability_and_network_isolation(monkeypatch):
    def deny(*args, **kwargs): raise AssertionError("network forbidden")
    result = four_trades()
    cfg = AnalyticsConfig(risk_free_rate_per_period=D("0.001"), annualization_factor=D("252"))
    inputs = (result, cfg, *result.closed_trades, *result.equity_curve)
    before = tuple(m.model_dump_json() for m in inputs)
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    report = analyze_performance(result, cfg)
    assert report == analyze_performance(result, cfg)
    assert report.model_dump_json() == analyze_performance(result, cfg).model_dump_json()
    assert tuple(m.model_dump_json() for m in inputs) == before
    models = (cfg, report, report.trades, report.returns, *report.returns.series,
              report.drawdown, *report.drawdown.series, *report.drawdown.episodes)
    assert report.drawdown.episodes  # ensure episode checks run
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, next(iter(type(model).model_fields)), None)
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unexpected": None})
    assert PerformanceReport.model_validate_json(report.model_dump_json()) == report


def test_input_config_and_result_independent_of_caller_context_and_traps():
    result = four_trades()
    cfg = AnalyticsConfig(risk_free_rate_per_period=D("0.00012345678901234567890123456789"), annualization_factor=D("252"))
    expected = analyze_performance(result, cfg)
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
        caller.traps[Inexact] = caller.traps[Rounded] = True
        assert analyze_performance(result, cfg) == expected
        assert PerformanceReport.model_validate_json(expected.model_dump_json()) == expected
        assert caller.prec == 2 and caller.rounding == ROUND_DOWN
        assert caller.traps[Inexact] and caller.traps[Rounded]


@pytest.mark.parametrize("change", ["capital", "final", "realized", "last", "point", "empty", "duplicate", "ordering", "nested_trade", "nested_fill", "nested_cost", "trade_quantity", "position_quantity", "position_time", "flat_unrealized", "float", "price_type"])
def test_malformed_unchecked_backtest_result_fails_explicitly(change):
    result = simulate(series=mid(bars((101, 99, 101, 110), (90, 100, 110, 100))), config=costs())
    update = {}
    if change == "capital": update = {"initial_capital": D("0")}
    elif change == "final": update = {"final_equity": D("1000")}
    elif change == "realized": update = {"realized_pnl": D("999")}
    elif change == "empty": update = {"equity_curve": ()}
    elif change == "duplicate": update = {"equity_curve": (result.equity_curve[0],) + result.equity_curve}
    elif change == "ordering": update = {"equity_curve": tuple(reversed(result.equity_curve))}
    elif change in ("last", "point"):
        curve = list(result.equity_curve)
        index = -1 if change == "last" else 0
        curve[index] = curve[index].model_copy(update={"equity": D("999")})
        update = {"equity_curve": tuple(curve)}
    elif change == "nested_trade": update = {"closed_trades": (result.closed_trades[0].model_copy(update={"net_pnl": D("999")}),)}
    elif change == "nested_fill": update = {"fills": (result.fills[0].model_copy(update={"execution_price": D("999")}),)}
    elif change == "nested_cost": update = {"execution_costs": result.execution_costs.model_copy(update={"spread": D("-1")})}
    elif change == "trade_quantity": update = {"quantity": D("3")}
    elif change == "position_quantity":
        position = result.open_position.model_copy(update={"quantity": D("3"), "entry_costs": result.open_position.entry_costs.model_copy(update={"spread_cost": D("3"), "slippage_cost": D("1.5")})})
        update = {"open_position": position}
    elif change == "position_time": update = {"open_position": result.open_position.model_copy(update={"entry_time": START + 10 * MINUTE})}
    elif change == "flat_unrealized": update = {"open_position": None}
    elif change == "float": update = {"realized_pnl": 9.5}
    else: update = {"price_type": "mid"}
    with pytest.raises(AnalyticsInputError): analyze_performance(result.model_copy(update=update))


def test_entrypoint_requires_canonical_result():
    result = simulate()
    with pytest.raises(AnalyticsError): analyze_performance(result.model_dump())
    with pytest.raises(AnalyticsInputError): analyze_performance(None)


@pytest.mark.parametrize("field", ["risk_free_rate_per_period", "annualization_factor"])
@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-Infinity"), 1, 1.0, True, "1"])
def test_config_strict_finite_decimal_fields(field, value):
    with pytest.raises(ValidationError): AnalyticsConfig(**{field: value})


@pytest.mark.parametrize("value", [D("0"), D("-1")])
def test_annualization_factor_positive(value):
    with pytest.raises(ValidationError): AnalyticsConfig(annualization_factor=value)


def test_timeframe_label_does_not_infer_annualization():
    report = analyze_performance(four_trades().model_copy(update={"timeframe": Timeframe.D1}))
    assert report.config.annualization_factor is None and report.returns.annualized_sharpe is None


def test_fresh_import_has_no_network():
    code = "import socket; deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); socket.socket=deny; socket.create_connection=deny; import quantlab.analytics"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
