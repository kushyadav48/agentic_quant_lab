"""Mandatory pre-fill gating, unchanged accounting and Phase 10 propagation."""
import socket
import subprocess
import sys

import pytest
from quantlab.analytics import analyze_performance
from quantlab.backtesting import (
    BacktestCompatibilityError, BacktestConfig, BacktestInputError, ExecutionCostConfig,
    SignalAction,
)
from quantlab.data import PriceType
from quantlab.risk import RiskAction as A, RiskConfig, RiskReason as R, RiskSide
from quantlab.strategies import Direction, DistanceUnit, FixedDistance
from quantlab.validation import (
    HoldoutConfig, ResearchValidationInputError, ValidationWindow, WalkForwardConfig, WalkForwardMode,
    run_holdout, run_parameter_robustness, run_walk_forward,
)
from tests.backtesting.helpers import CONFIG, D, INSTRUMENT, bars, simulate, strategy
from tests.validation.test_validation import variants


def config(risk=None, costs=None):
    return BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        risk=RiskConfig() if risk is None else risk,
        execution_costs=ExecutionCostConfig() if costs is None else costs)


def costs():
    return ExecutionCostConfig(spread=D("2"), slippage=D("0.5"),
        commission_per_unit=D("0.25"), fixed_fee_per_fill=D("1"))


def mid(series):
    return tuple(b.model_copy(update={"price_type": PriceType.MID}) for b in series)


@pytest.mark.parametrize("direction,closes,opens", [
    (Direction.LONG, (101, 110, 99, 90), (80, 100, 111, 90)),
    (Direction.SHORT, (99, 90, 101, 110), (120, 100, 89, 110)),
])
@pytest.mark.parametrize("with_costs", [False, True])
def test_defaults_preserve_hand_computed_phase7_8_economics(direction, closes, opens, with_costs):
    series = mid(bars(closes, opens)) if with_costs else bars(closes, opens)
    cfg = config(costs=costs() if with_costs else None)
    spec = strategy(direction)
    result = simulate(spec, series, config=cfg)
    unrestricted = simulate(spec, series, config=config(risk=RiskConfig(max_position_quantity=D("2"),
        max_notional_exposure=D("200"), max_equity_fraction=D("0.20"),
        minimum_equity=D("0"), max_drawdown_fraction=D("1")), costs=cfg.execution_costs))
    # All pre-Phase-11 result fields, plus analytics, remain economically identical.
    assert result.model_dump(exclude={"risk", "risk_decisions"}) == unrestricted.model_dump(exclude={"risk", "risk_decisions"})
    assert analyze_performance(result) == analyze_performance(unrestricted)
    assert result.final_equity == D("971" if with_costs else "980")
    assert result.closed_trades[0].net_pnl == D("-29" if with_costs else "-20")
    assert len(result.risk_decisions) == 1 and result.risk_decisions[0].action is A.ALLOW
    assert all(fill.quantity == CONFIG.quantity for fill in result.fills)
    assert result == simulate(spec, series, config=cfg)
    assert result.model_dump_json() == simulate(spec, series, config=cfg).model_dump_json()


@pytest.mark.parametrize("direction,closes", [(Direction.LONG, (101, 110)), (Direction.SHORT, (99, 90))])
def test_rejected_entry_retains_signal_but_no_position_pnl_or_any_cost(direction, closes):
    cfg = config(risk=RiskConfig(max_position_quantity=D("1")), costs=costs())
    series = mid(bars(closes, (100, 100)))
    result = simulate(strategy(direction), series, config=cfg)
    assert result.signals[0] == simulate(strategy(direction), series).signals[0]
    # Rejection leaves the account flat, so a later close may propose again.
    assert len(result.signals) == 2
    assert result.fills == result.closed_trades == () and result.open_position is None
    assert result.realized_pnl == result.unrealized_pnl == 0 and result.final_equity == D("1000")
    assert all(point.equity == D("1000") for point in result.equity_curve)
    decision, = result.risk_decisions
    assert decision.action is A.REJECT and decision.reasons == (R.MAX_POSITION_QUANTITY,)
    assert decision.requested_quantity == D("2") and decision.approved_quantity == 0
    assert decision.signal_time == result.signals[0].signal_time
    assert decision.execution_time == series[1].start_time
    assert decision.side is (RiskSide.LONG if direction is Direction.LONG else RiskSide.SHORT)
    assert decision.reference_price == D("100") and decision.current_equity == D("1000")
    assert decision.running_peak_equity == D("1000")
    report = analyze_performance(result)
    assert report.trades.closed_trade_count == 0 and report.returns.cumulative_return == 0


def test_risk_rejects_before_cost_calculation_even_if_hypothetical_price_invalid():
    cfg = config(risk=RiskConfig(max_position_quantity=D("1")),
        costs=ExecutionCostConfig(slippage=D("200")))
    result = simulate(strategy(Direction.SHORT), bars((99, 90), (100, 100)), config=cfg)
    assert result.fills == () and result.risk_decisions[0].action is A.REJECT
    with pytest.raises(BacktestInputError, match="nonpositive"):
        simulate(strategy(Direction.SHORT), bars((99, 90), (100, 100)),
            config=config(costs=cfg.execution_costs))


def test_risk_is_pre_entry_cost_and_reference_price_based():
    cfg = config(risk=RiskConfig(max_notional_exposure=D("200"), max_equity_fraction=D("0.20"),
        minimum_equity=D("999")), costs=costs())
    result = simulate(strategy(no_exit=True), mid(bars((101, 100), (80, 100))), config=cfg)
    decision, = result.risk_decisions
    assert decision.action is A.ALLOW and decision.current_equity == D("1000")
    assert decision.reference_price == D("100")
    fill, = result.fills
    assert fill.execution_price == D("101.5") and fill.costs.explicit_cost == D("1.5")
    assert result.realized_pnl == D("-1.5") and result.unrealized_pnl == D("-3")
    assert result.final_equity == D("995.5") and result.open_position is not None


@pytest.mark.parametrize("limit", [RiskConfig(max_notional_exposure=D("200")),
    RiskConfig(max_equity_fraction=D("0.20")), RiskConfig(max_drawdown_fraction=D("0.10"))])
def test_entry_decision_never_reads_execution_bar_high_low_close_or_publication(limit):
    series = bars((101, 110, 110), (80, 100, 100))
    cfg = config(risk=limit)
    expected = simulate(strategy(no_exit=True), series, config=cfg)
    changed = list(series)
    changed[1] = changed[1].model_copy(update={"high": D("9999"), "low": D("1"),
        "close": D("5000"), "available_at": series[-1].end_time})
    actual = simulate(strategy(no_exit=True), changed, config=cfg)
    assert actual.risk_decisions == expected.risk_decisions
    assert actual.fills == expected.fills


def test_next_open_not_signal_close_controls_exposure_after_a_gap():
    first, next_bar = bars((101, 110), (80, 150))
    result = simulate(series=(first, next_bar.model_copy(update={
        "start_time": next_bar.start_time + (next_bar.end_time - next_bar.start_time),
        "end_time": next_bar.end_time + (next_bar.end_time - next_bar.start_time),
        "available_at": next_bar.available_at + (next_bar.end_time - next_bar.start_time)})),
        config=config(risk=RiskConfig(max_notional_exposure=D("250"))))
    decision, = result.risk_decisions
    assert decision.reference_price == D("150") and decision.action is A.REJECT
    assert decision.execution_time > decision.signal_time


@pytest.mark.parametrize("risk,reason", [
    (RiskConfig(max_equity_fraction=D("0.21")), R.MAX_EQUITY_FRACTION),
    (RiskConfig(minimum_equity=D("900")), R.MINIMUM_EQUITY),
    (RiskConfig(max_drawdown_fraction=D("0.10")), R.MAX_DRAWDOWN),
])
def test_next_entry_consumes_actual_flat_equity_after_realized_loss(risk, reason):
    series = bars((101, 99, 101, 110), (80, 100, 50, 100))
    result = simulate(series=series, config=config(risk=risk))
    assert [d.action for d in result.risk_decisions] == [A.ALLOW, A.REJECT]
    assert result.risk_decisions[-1].current_equity == D("900")
    assert result.risk_decisions[-1].running_peak_equity == D("1000")
    assert result.risk_decisions[-1].reasons == (reason,)
    assert [f.action for f in result.fills] == [SignalAction.ENTER_LONG, SignalAction.EXIT_LONG]
    assert result.realized_pnl == D("-100") and result.open_position is None


def test_drawdown_peak_includes_prior_known_unrealized_gain():
    result = simulate(series=bars((101, 200, 99, 101, 110), (80, 100, 100, 100, 100)),
        config=config(risk=RiskConfig(max_drawdown_fraction=D("0.10"))))
    assert result.equity_curve[1].equity == D("1200")
    decision = result.risk_decisions[-1]
    assert decision.running_peak_equity == D("1200") and decision.current_equity == D("1000")
    assert decision.action is A.REJECT and decision.reasons == (R.MAX_DRAWDOWN,)


def test_delayed_retrospective_close_mark_never_enters_runtime_peak_even_when_later_published():
    series = list(bars((101, 200, 99, 101, 110), (80, 100, 100, 100, 100)))
    series[1] = series[1].model_copy(update={"available_at": series[3].end_time})
    result = simulate(series=series, config=config(risk=RiskConfig(max_drawdown_fraction=D("0.10"))))
    assert result.equity_curve[1].equity == D("1200")  # reporting remains retrospective
    assert [d.action for d in result.risk_decisions] == [A.ALLOW, A.ALLOW]
    assert result.risk_decisions[-1].running_peak_equity == D("1000")
    changed = list(series)
    changed[1] = changed[1].model_copy(update={"high": D("5000"), "close": D("5000")})
    assert simulate(series=changed, config=config(risk=result.risk)).risk_decisions == result.risk_decisions


def test_current_flat_equity_can_raise_peak_after_profitable_exit():
    result = simulate(series=bars((101, 99, 101, 110), (80, 100, 150, 100)),
        config=config(risk=RiskConfig(max_drawdown_fraction=D("0.10"))))
    assert result.risk_decisions[-1].current_equity == D("1100")
    assert result.risk_decisions[-1].running_peak_equity == D("1100")
    assert result.risk_decisions[-1].action is A.ALLOW


@pytest.mark.parametrize("direction,closes,opens", [
    (Direction.LONG, (101, 50, 50), (80, 100, 40)),
    (Direction.SHORT, (99, 150, 150), (120, 100, 160)),
])
def test_exits_never_blocked_when_equity_and_drawdown_breached(direction, closes, opens):
    result = simulate(strategy(direction), bars(closes, opens), config=config(
        risk=RiskConfig(minimum_equity=D("950"), max_drawdown_fraction=D("0.01"))))
    assert len(result.risk_decisions) == 1 and result.risk_decisions[0].action is A.ALLOW
    assert len(result.fills) == 2 and len(result.closed_trades) == 1
    assert result.open_position is None and result.final_equity == D("880")


def test_breach_does_not_liquidate_position_or_fabricate_end_cost():
    cfg = config(risk=RiskConfig(minimum_equity=D("950"), max_drawdown_fraction=D("0.01")),
        costs=ExecutionCostConfig(fixed_fee_per_fill=D("1")))
    result = simulate(strategy(no_exit=True), bars((101, 50, 25), (80, 100, 30)), config=cfg)
    assert len(result.risk_decisions) == len(result.fills) == 1
    assert result.open_position is not None and result.closed_trades == ()
    assert result.realized_pnl == D("-1") and result.final_equity == D("849")
    assert result.unrealized_pnl == D("-150")


def test_rejections_are_consumed_new_close_can_retry_final_signal_not_evaluated():
    series = bars((110,) * 5, (100,) * 5)
    result = simulate(series=series, config=config(risk=RiskConfig(max_position_quantity=D("1"))))
    assert len(result.signals) == 5 and len(result.risk_decisions) == 4
    assert result.fills == ()
    assert tuple(d.signal_time for d in result.risk_decisions) == tuple(b.end_time for b in series[:-1])
    assert tuple(d.execution_time for d in result.risk_decisions) == tuple(b.start_time for b in series[1:])
    assert all(d.action is A.REJECT and d.current_equity == D("1000") for d in result.risk_decisions)


def test_rejected_signal_is_not_carried_to_an_unrequested_later_open():
    result = simulate(series=bars((101, 99, 99), (80, 150, 100)),
        config=config(risk=RiskConfig(max_notional_exposure=D("200"))))
    assert len(result.signals) == len(result.risk_decisions) == 1
    assert result.fills == () and result.risk_decisions[0].action is A.REJECT


def test_quantity_increment_contract_stays_exact_without_rounding_or_reduction():
    instrument = INSTRUMENT.model_copy(update={"quantity_increment": D("0.1")})
    cfg = BacktestConfig(initial_capital=D("1000"), quantity=D("1.3"),
        risk=RiskConfig(max_position_quantity=D("1.25")))
    result = simulate(instrument=instrument, config=cfg)
    assert result.fills == () and result.risk_decisions[0].approved_quantity == 0
    with pytest.raises(BacktestInputError, match="multiple"):
        simulate(instrument=instrument, config=cfg.model_copy(update={"quantity": D("1.37")}))


def test_future_suffix_cannot_change_risk_prefix_and_inputs_not_mutated():
    series = bars((101, 99, 101, 110, 90, 110, 110), (80, 100, 50, 100, 100, 100, 100))
    spec = strategy()
    cfg = config(risk=RiskConfig(max_drawdown_fraction=D("0.10")))
    before = tuple(b.model_dump_json() for b in series), spec.model_dump_json(), cfg.model_dump_json()
    complete = simulate(spec, series, config=cfg)
    for length in range(1, len(series) + 1):
        result = simulate(spec, series[:length], config=cfg)
        assert result.risk_decisions == tuple(d for d in complete.risk_decisions
            if d.execution_time < series[length - 1].end_time)
        assert result.equity_curve == complete.equity_curve[:length]
        assert result.signals == tuple(s for s in complete.signals if s.signal_time <= series[length - 1].end_time)
    assert before == (tuple(b.model_dump_json() for b in series), spec.model_dump_json(), cfg.model_dump_json())


def test_stop_execution_remains_explicitly_unsupported():
    spec = strategy(stop_loss=FixedDistance(value=D("1"), unit=DistanceUnit.PRICE))
    with pytest.raises(BacktestCompatibilityError, match="stop_loss"):
        simulate(spec, config=config(risk=RiskConfig(max_notional_exposure=D("200"))))


def test_holdout_preserves_risk_and_each_segment_starts_with_independent_peak():
    series = bars((101, 200, 99, 101, 110, 110), (80, 100, 100, 80, 100, 100))
    cfg = config(risk=RiskConfig(max_drawdown_fraction=D("0.10"), max_notional_exposure=D("200")))
    report = run_holdout(strategy(), series, instrument=INSTRUMENT, config=cfg,
        split=HoldoutConfig(train=ValidationWindow(start=0, end=3), test=ValidationWindow(start=3, end=6)))
    assert report.backtest_config.risk == cfg.risk
    for segment in (report.in_sample, report.out_of_sample):
        result = segment.backtest
        assert result.risk == cfg.risk
        assert result.risk_decisions[0].running_peak_equity == D("1000")
        assert result.risk_decisions[0].action is A.ALLOW
        assert segment.performance == analyze_performance(result)
    assert report.in_sample.backtest.equity_curve[1].equity == D("1200")
    assert report.out_of_sample.backtest.risk_decisions[0].current_equity == D("1000")


def test_holdout_rejects_entries_in_both_segments():
    cfg = config(risk=RiskConfig(max_position_quantity=D("1")), costs=costs())
    report = run_holdout(strategy(), mid(bars((110,) * 6)), instrument=INSTRUMENT, config=cfg,
        split=HoldoutConfig(train=ValidationWindow(start=0, end=3), test=ValidationWindow(start=3, end=6)))
    for segment in (report.in_sample, report.out_of_sample):
        assert segment.backtest.risk == cfg.risk and segment.backtest.fills == ()
        assert len(segment.backtest.risk_decisions) == 2
        assert all(d.action is A.REJECT for d in segment.backtest.risk_decisions)
        assert segment.backtest.execution_costs == cfg.execution_costs
        assert segment.backtest.realized_pnl == 0 and segment.backtest.final_equity == D("1000")


@pytest.mark.parametrize("mode", tuple(WalkForwardMode))
def test_walk_forward_risk_state_resets_for_each_train_test_and_fold(mode):
    series = bars((101, 99, 101) * 3, (80, 100, 50) * 3)
    cfg = config(risk=RiskConfig(max_drawdown_fraction=D("0.10")))
    report = run_walk_forward(strategy(), series, instrument=INSTRUMENT, config=cfg,
        walk_forward=WalkForwardConfig(train_size=3, test_size=3, step_size=3, mode=mode))
    assert report.backtest_config.risk == cfg.risk and report.fold_count == 2
    for fold in report.folds:
        for segment in (fold.in_sample, fold.out_of_sample):
            assert segment.backtest.risk == cfg.risk
            first = segment.backtest.risk_decisions[0]
            assert first.current_equity == first.running_peak_equity == D("1000")
            assert first.action is A.ALLOW
        assert fold.out_of_sample.backtest.realized_pnl == D("-100")
        assert len(fold.out_of_sample.backtest.fills) == 2


def test_robustness_every_approved_variant_uses_same_risk_and_independent_state():
    cfg = config(risk=RiskConfig(max_position_quantity=D("1")))
    report = run_parameter_robustness(variants(), bars((115,) * 3),
        baseline_candidate_id="baseline", window=ValidationWindow(start=0, end=3),
        instrument=INSTRUMENT, config=cfg)
    assert report.backtest_config.risk == cfg.risk and report.candidate_count == 3
    for candidate in report.candidates:
        result = candidate.evaluation.backtest
        assert result.risk == cfg.risk and result.fills == ()
        assert len(result.risk_decisions) == 2
        assert all(d.action is A.REJECT and d.current_equity == d.running_peak_equity == D("1000")
            for d in result.risk_decisions)
    # Wire audit is retained all the way through Phase 10 reports.
    assert type(report).model_validate_json(report.model_dump_json()) == report


def test_no_network_import_or_runtime(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    result = simulate(config=config(risk=RiskConfig(max_notional_exposure=D("1"))))
    assert result.risk_decisions[0].action is A.REJECT
    analyze_performance(result)
    script = "import socket; deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); socket.socket=deny; socket.create_connection=deny; import quantlab.risk; import quantlab.backtesting"
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("runner", ["holdout", "walk_forward", "robustness"])
def test_phase10_public_boundaries_revalidate_malformed_nested_risk(runner):
    malformed = config().model_copy(update={"risk": RiskConfig().model_copy(
        update={"max_notional_exposure": D("-1")})})
    series = bars((110,) * 6)
    kwargs = dict(instrument=INSTRUMENT, config=malformed)
    with pytest.raises(ResearchValidationInputError):
        if runner == "holdout":
            run_holdout(strategy(), series, split=HoldoutConfig(
                train=ValidationWindow(start=0, end=3), test=ValidationWindow(start=3, end=6)), **kwargs)
        elif runner == "walk_forward":
            run_walk_forward(strategy(), series, walk_forward=WalkForwardConfig(
                train_size=3, test_size=3, step_size=3), **kwargs)
        else:
            run_parameter_robustness(variants(), series, baseline_candidate_id="baseline",
                window=ValidationWindow(start=0, end=6), **kwargs)
