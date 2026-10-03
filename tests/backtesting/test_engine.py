"""Causality, hand-calculated lifecycles, approval, and strict input contracts."""
from datetime import time, timedelta
from decimal import Context, DefaultContext, ROUND_DOWN, getcontext, localcontext
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError
from quantlab.backtesting import (
    BacktestCompatibilityError, BacktestConfig, BacktestInputError, BacktestResult,
    BacktestSignalConflictError, PositionSide, SignalAction as A,
)
from quantlab.data import PriceType, Timeframe
from quantlab.strategies import (
    Comparison as C, ConstantOperand, Direction, DistanceUnit, FeatureOperand,
    FeatureArgument, FeatureReference, FeatureType, FixedDistance, MarketField, MarketOperand,
    SessionFilter, SideRules,
)
from .helpers import (
    CONFIG, D, INSTRUMENT, MINUTE, START, approve, bars, group, observations,
    reference, rule, simulate, strategy,
)


@pytest.mark.parametrize("direction,closes,opens,side,entry,exit", [
    (Direction.LONG,(101,110,99,90),(80,100,111,90),PositionSide.LONG,A.ENTER_LONG,A.EXIT_LONG),
    (Direction.SHORT,(99,90,101,110),(120,100,89,110),PositionSide.SHORT,A.ENTER_SHORT,A.EXIT_SHORT),
])
def test_hand_computed_lifecycle(direction,closes,opens,side,entry,exit):
    series = bars(closes,opens)
    spec = strategy(direction)
    result = simulate(spec,series)
    assert [s.action for s in result.signals]==[entry,exit]
    assert [s.signal_time for s in result.signals]==[series[0].end_time,series[2].end_time]
    assert [(f.execution_time,f.execution_price) for f in result.fills]==[
        (series[1].start_time,D("100")),(series[3].start_time,D(opens[3]))]
    assert all(f.quantity==D("2") for f in result.fills)
    trade, = result.closed_trades
    assert trade.side is side
    assert trade.entry_signal_time==series[0].end_time
    assert trade.entry_time==series[1].start_time
    assert trade.entry_price==D("100")
    assert trade.exit_signal_time==series[2].end_time
    assert trade.exit_time==series[3].start_time
    assert trade.exit_price==D("90" if side is PositionSide.LONG else "110")
    assert trade.quantity==D("2") and trade.gross_pnl==D("-20")
    assert result.open_position is None
    assert result.realized_pnl==D("-20") and result.unrealized_pnl==0
    assert result.final_equity==D("980")
    assert [p.unrealized_pnl for p in result.equity_curve]==[D("0"),D("20"),D("-2"),D("0")]
    assert [p.equity for p in result.equity_curve]==[D("1000"),D("1020"),D("998"),D("980")]
    assert result.strategy_content_digest==spec.content_digest
    assert result.strategy_id==spec.strategy_id and result.strategy_version==spec.version
    for signal in result.signals:
        assert signal.strategy_digest==spec.content_digest and signal.instrument_id==INSTRUMENT.instrument_id


@pytest.mark.parametrize("direction,closes,opens", [
    (Direction.LONG,(101,99,99),(101,100,110)),
    (Direction.SHORT,(99,101,101),(99,100,90)),
])
def test_exact_positive_closed_pnl(direction,closes,opens):
    result = simulate(strategy(direction),bars(closes,opens))
    assert result.closed_trades[0].gross_pnl==D("20")
    assert result.realized_pnl==D("20") and result.final_equity==D("1020")
    assert result.open_position is None


@pytest.mark.parametrize("direction,closes,action", [
    (Direction.LONG,(101,110),A.ENTER_LONG), (Direction.SHORT,(99,90),A.ENTER_SHORT),
])
def test_no_exit_keeps_final_position_and_unrealized(direction,closes,action):
    result = simulate(strategy(direction,no_exit=True),bars(closes,opens=(101,100)))
    assert len(result.signals)==len(result.fills)==1
    assert result.signals[0].action is action
    assert result.closed_trades==()
    assert result.open_position.entry_price==D("100")
    assert result.realized_pnl==D("0")
    assert result.unrealized_pnl==D("20") and result.final_equity==D("1020")


def test_pending_action_executes_before_new_close_and_final_exit_stays_open():
    series = bars((101,99),opens=(50,100))
    result = simulate(series=series)
    assert [s.action for s in result.signals]==[A.ENTER_LONG,A.EXIT_LONG]
    assert len(result.fills)==1 and result.closed_trades==()
    assert result.fills[0].execution_price==series[1].open
    assert result.fills[0].execution_price!=series[0].open
    assert result.open_position is not None
    assert result.unrealized_pnl==D("-2") and result.final_equity==D("998")


def test_one_bar_entry_records_signal_but_never_fills():
    result = simulate(series=bars((101,)))
    assert len(result.signals)==1
    assert result.fills==result.closed_trades==()
    assert result.open_position is None and result.final_equity==CONFIG.initial_capital


def test_one_bar_crossing_is_unavailable():
    result = simulate(strategy(entry=group(rule(C.CROSSES_ABOVE))),bars((101,)))
    assert result.signals==result.fills==result.closed_trades==()


def test_empty_series_is_consistently_rejected():
    with pytest.raises(BacktestInputError,match="at least one"): simulate(series=())


@pytest.mark.parametrize("prices,action", [((101,110),A.ENTER_LONG),((99,90),A.ENTER_SHORT)])
def test_both_selects_exactly_one_side(prices,action):
    result = simulate(strategy(Direction.BOTH),bars(prices))
    assert result.signals[0].action is action
    assert len(result.fills)==1


def test_simultaneous_both_entries_raise_clear_conflict():
    spec = strategy(Direction.BOTH,entry=group(rule(C.EQ,left=ConstantOperand(value=True),
        right=ConstantOperand(value=True))))
    with pytest.raises(BacktestSignalConflictError,match="simultaneous long/short"):
        simulate(spec,bars((100,)))


@pytest.mark.parametrize("direction,prices", [(Direction.LONG,(101,102,103,104)),(Direction.SHORT,(99,98,97,96))])
def test_no_pyramiding_or_duplicate_entry(direction,prices):
    result = simulate(strategy(direction,no_exit=True),bars(prices))
    assert len(result.signals)==len(result.fills)==1
    assert result.open_position.quantity==CONFIG.quantity


def test_both_close_then_future_close_entry_without_same_open_reversal():
    series = bars((101,99,99,99,101,101),opens=(90,100,90,80,70,90))
    result = simulate(strategy(Direction.BOTH),series)
    assert [s.action for s in result.signals]==[A.ENTER_LONG,A.EXIT_LONG,A.ENTER_SHORT,A.EXIT_SHORT,A.ENTER_LONG]
    assert [f.action for f in result.fills]==[A.ENTER_LONG,A.EXIT_LONG,A.ENTER_SHORT,A.EXIT_SHORT]
    assert [f.execution_time for f in result.fills]==[series[i].start_time for i in (1,2,3,5)]
    assert len({f.execution_time for f in result.fills})==len(result.fills)
    assert len(result.closed_trades)==2 and result.open_position is None
    assert result.realized_pnl==D("-40") and result.final_equity==D("960")


def test_realized_plus_open_unrealized_equity():
    series = bars((101,99,101,110),opens=(90,100,110,100))
    result = simulate(series=series)
    assert result.realized_pnl==D("20") and result.unrealized_pnl==D("20")
    assert result.final_equity==D("1040")
    for p in result.equity_curve:
        assert p.equity==CONFIG.initial_capital+p.realized_pnl+p.unrealized_pnl


def test_feature_warmup_cannot_accidentally_create_trades():
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),features=(reference(period=3),))
    series = bars((110,110,110))
    result = simulate(spec,series,observations(spec,series))
    assert [s.source_bar_start for s in result.signals]==[series[2].start_time]
    assert result.fills==() and result.open_position is None


def test_missing_exact_feature_timestamp_never_uses_older_feature():
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),features=(reference(period=1),))
    series = bars((99,110,110))
    features = observations(spec,series)[:1]
    assert simulate(spec,series,features).signals==()


def test_late_feature_does_not_influence_historical_decision():
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),features=(reference(period=1),))
    series = bars((110,99))
    features = list(observations(spec,series))
    features[0] = features[0].model_copy(update={"available_at":series[1].end_time})
    assert simulate(spec,series,features).signals==()  # no retroactive first signal


@pytest.mark.parametrize("entry", [group(rule()),group(rule(offset=1)),
    group(rule(C.EQ,left=ConstantOperand(value=True),right=ConstantOperand(value=True)))])
def test_delayed_current_bar_disables_its_close_decision(entry):
    series = list(bars((110,110)))
    series[1] = series[1].model_copy(update={"available_at":START+timedelta(days=1)})
    result = simulate(strategy(entry=entry,no_exit=True),series)
    assert all(s.source_bar_start!=series[1].start_time for s in result.signals)


def test_delayed_market_history_unavailable_until_current_decision_time():
    series = list(bars((110,99,99)))
    series[0] = series[0].model_copy(update={"available_at":series[1].end_time})
    spec = strategy(entry=group(rule(offset=1)),no_exit=True)
    result = simulate(spec,series)
    assert [s.source_bar_start for s in result.signals]==[series[1].start_time]
    assert result.fills[0].execution_time==series[2].start_time


def test_crossing_uses_previous_dependencies_available_by_current_decision():
    series = list(bars((100,110)))
    spec = strategy(entry=group(rule(C.CROSSES_ABOVE,left=FeatureOperand(feature_id="fast"))),
        features=(reference(period=1),))
    features = list(observations(spec,series))
    features[0] = features[0].model_copy(update={"available_at":series[1].end_time})
    assert len(simulate(spec,series,features).signals)==1
    features[0] = features[0].model_copy(update={"available_at":series[1].end_time+MINUTE})
    assert simulate(spec,series,features).signals==()


def test_future_bars_and_observations_cannot_change_prefix_signals_or_equity():
    series = bars((90,100,110,120,90,80,110))
    spec = strategy(entry=group(rule(C.CROSSES_ABOVE,left=FeatureOperand(feature_id="fast"))),
        features=(reference(),))
    features = observations(spec,series)
    complete = simulate(spec,series,features)
    for length in range(1,len(series)+1):
        prefix = series[:length]
        causal = tuple(o for o in features if o.timestamp<=prefix[-1].end_time)
        result = simulate(spec,prefix,causal)
        # Supplying the future observations too cannot change this prefix.
        assert simulate(spec,prefix,features)==result
        assert result.signals==tuple(s for s in complete.signals if s.signal_time<=prefix[-1].end_time)
        assert result.equity_curve==complete.equity_curve[:length]
        # A signal at the prefix boundary stays unfilled until the next input bar.
        assert result.fills==tuple(f for f in complete.fills if f.execution_time<prefix[-1].end_time)


def test_next_available_open_after_gap_and_mixed_provenance():
    series = bars((101,110,120))
    last = series[2].model_copy(update={"source_id":"other","dataset_id":"other"})
    result = simulate(strategy(no_exit=True),(series[0],last))
    assert result.fills[0].execution_time==last.start_time
    assert result.fills[0].execution_price==last.open


@pytest.mark.parametrize("state", ["draft","validated","revision"])
def test_unapproved_or_revised_strategy_rejected_without_mutation(state):
    draft = strategy(approved=False)
    spec = draft if state=="draft" else (draft.mark_validated() if state=="validated" else approve(draft).revise(draft.content))
    before = spec.model_dump_json()
    with pytest.raises(BacktestCompatibilityError,match="APPROVED"): simulate(spec)
    assert spec.model_dump_json()==before
    if state=="revision": assert simulate(approve(spec))


@pytest.mark.parametrize("change", ["digest","version","content","state"])
def test_unchecked_forged_approval_revalidated(change):
    spec = strategy()
    if change=="digest": spec = spec.model_copy(update={"approval":spec.approval.model_copy(update={"content_digest":"0"*64})})
    elif change=="version": spec = spec.model_copy(update={"version":2})
    elif change=="content": spec = spec.model_copy(update={"content":spec.content.model_copy(update={"name":"changed"})})
    else: spec = spec.model_copy(update={"state":"approved"})
    with pytest.raises(BacktestCompatibilityError,match="contract"): simulate(spec)


@pytest.mark.parametrize("field,value", [
    ("stop_loss",FixedDistance(value=D("1"),unit=DistanceUnit.PRICE)),
    ("take_profit",FixedDistance(value=D("1"),unit=DistanceUnit.PRICE)),
    ("session",SessionFilter(start_utc=time(8),end_utc=time(16),weekdays=(0,))),
    ("sizing_reference","sizing:future"),
])
def test_unsupported_strategy_content_explicitly_rejected(field,value):
    with pytest.raises(BacktestCompatibilityError,match=field): simulate(strategy(**{field:value}))


@pytest.mark.parametrize("field", [MarketField.BID,MarketField.ASK])
@pytest.mark.parametrize("in_exit", [False,True])
def test_bid_ask_rejected_anywhere_in_strategy(field,in_exit):
    kwargs = {"exit" if in_exit else "entry":group(rule(left=MarketOperand(field=field)))}
    with pytest.raises(BacktestCompatibilityError,match="BID/ASK"): simulate(strategy(**kwargs))


@pytest.mark.parametrize("kind", [FeatureType.ML_SIGNAL,FeatureType.LEVEL])
def test_unsupported_feature_types_rejected(kind):
    feature = FeatureReference(feature_id="future",feature_type=kind)
    with pytest.raises(BacktestCompatibilityError,match="features"): simulate(strategy(features=(feature,)))


@pytest.mark.parametrize("feature", [FeatureReference(feature_id="unknown",feature_type=FeatureType.INDICATOR),
    reference(timeframe=Timeframe.M5),reference(period=0)])
def test_incompatible_registered_feature_declarations(feature):
    with pytest.raises(BacktestCompatibilityError,match="features"): simulate(strategy(features=(feature,)))


@pytest.mark.parametrize("kwargs", [{"instruments":("other",)},
    {"instruments":(INSTRUMENT.instrument_id,"other")},{"timeframe":Timeframe.M5}])
def test_strategy_series_compatibility(kwargs):
    with pytest.raises(BacktestCompatibilityError): simulate(strategy(**kwargs))


@pytest.mark.parametrize("change", ["ordering","duplicate","instrument","timeframe","price_type",
    "overlap","ohlc","float","naive","early_availability","mapping","object"])
def test_reject_invalid_bar_input(change):
    series = list(bars())
    if change=="ordering": series[1],series[2] = series[2],series[1]
    elif change=="duplicate": series[1] = series[0]
    elif change=="mapping": series[1] = series[1].model_dump()
    elif change=="object": series[1] = object()
    else:
        updates = {"instrument":{"instrument_id":"other"},"timeframe":{"timeframe":Timeframe.M5},
            "price_type":{"price_type":PriceType.BID},"overlap":{"start_time":START+MINUTE/2},
            "ohlc":{"high":D("1")},"float":{"close":110.0},"naive":{"start_time":START.replace(tzinfo=None)},
            "early_availability":{"available_at":START}}[change]
        series[1] = series[1].model_copy(update=updates)
    with pytest.raises(BacktestInputError): simulate(series=series)


@pytest.mark.parametrize("change", ["duplicate","ordering","instrument","timeframe","price_type",
    "implementation","parameters","undeclared","nan","float","naive","early_availability","mapping"])
def test_reject_invalid_feature_input(change):
    series = bars()
    spec = strategy(features=(reference(period=1),))
    features = list(observations(spec,series))
    if change=="duplicate": features[1] = features[0]
    elif change=="ordering": features[1],features[2] = features[2],features[1]
    elif change=="mapping": features[1] = features[1].model_dump()
    else:
        updates = {"instrument":{"instrument_id":"other"},"timeframe":{"timeframe":Timeframe.M5},
            "price_type":{"price_type":PriceType.BID},"implementation":{"implementation_id":"ema"},
            "parameters":{"parameters":()},"undeclared":{"feature_id":"other"},
            "nan":{"value":D("NaN")},"float":{"value":110.0},
            "naive":{"timestamp":START.replace(tzinfo=None)},"early_availability":{"available_at":START}}[change]
        features[1] = features[1].model_copy(update=updates)
    with pytest.raises(BacktestInputError): simulate(spec,series,features)


@pytest.mark.parametrize("field", ["initial_capital","quantity"])
@pytest.mark.parametrize("value", [D("0"),D("-1"),D("NaN"),D("Infinity"),1,1.0,True,"1"])
def test_strict_positive_finite_decimal_config(field,value):
    values = CONFIG.model_dump()
    values[field] = value
    with pytest.raises(ValidationError): BacktestConfig(**values)


def test_quantity_increment_reused_exactly():
    with pytest.raises(BacktestInputError,match="quantity_increment"):
        simulate(config=BacktestConfig(initial_capital=D("1000"),quantity=D("1.5")))
    fine = INSTRUMENT.model_copy(update={"quantity_increment":D("0.1")})
    assert simulate(instrument=fine,config=BacktestConfig(initial_capital=D("1000"),quantity=D("0.3")))
    with pytest.raises(BacktestInputError,match="quantity_increment"):
        simulate(instrument=fine,config=BacktestConfig(initial_capital=D("1000"),quantity=D("0.30000000000000000000000000000000001")))


def test_unchecked_bad_config_and_instrument_rejected():
    with pytest.raises(BacktestInputError): simulate(config=CONFIG.model_copy(update={"quantity":1.0}))
    with pytest.raises(BacktestInputError): simulate(instrument=INSTRUMENT.model_copy(update={"tick_size":D("0")}))


def test_repeatability_json_roundtrips_immutability_and_no_input_mutation(monkeypatch):
    def deny(*args,**kwargs): raise AssertionError("network forbidden")
    monkeypatch.setattr(socket,"socket",deny)
    monkeypatch.setattr(socket,"create_connection",deny)
    spec = strategy(features=(reference(),))
    series = list(bars((101,99,101,110),opens=(90,100,110,100)))
    features = list(observations(spec,series))
    inputs = [spec,CONFIG,INSTRUMENT,*series,*features]
    before = tuple(m.model_dump_json() for m in inputs)
    result = simulate(spec,series,features)
    assert result==simulate(spec,tuple(series),tuple(features))
    assert result.model_dump_json()==simulate(spec,series,features).model_dump_json()
    assert tuple(m.model_dump_json() for m in inputs)==before
    assert BacktestResult.model_validate_json(result.model_dump_json())==result
    models = [CONFIG,result,*result.signals,*result.fills,*result.closed_trades,*result.equity_curve,result.open_position]
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json())==model
        assert model.model_config["extra"]=="forbid"
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError,match="frozen"): setattr(model,field,getattr(model,field))
    assert type(result.realized_pnl) is type(result.unrealized_pnl) is type(result.final_equity) is D


def test_accounting_independent_of_ambient_decimal_context():
    series = bars((101,99,101,110),opens=(90,100,110,100))
    expected = simulate(series=series)
    with localcontext(Context(prec=2,rounding=ROUND_DOWN)):
        assert simulate(series=series)==expected


def test_fresh_import_no_network():
    code = "import socket; deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); socket.socket=deny; socket.create_connection=deny; import quantlab.backtesting"
    subprocess.run([sys.executable,"-c",code],check=True,capture_output=True,text=True)


@pytest.mark.parametrize("entry", [group(rule()),
    group(rule(C.EQ,left=ConstantOperand(value=True),right=ConstantOperand(value=True)))])
def test_first_delayed_bar_cannot_emit_a_signal_even_with_constant_rules(entry):
    series = bars((110,))
    delayed = series[0].model_copy(update={"available_at":series[0].end_time+MINUTE})
    result = simulate(strategy(entry=entry), (delayed,))
    assert result.signals==result.fills==() and result.final_equity==CONFIG.initial_capital


def test_delayed_market_operand_cannot_be_used_from_prior_bar():
    series = list(bars((110,99)))
    series[0] = series[0].model_copy(update={"available_at":START+timedelta(days=1)})
    result = simulate(strategy(entry=group(rule(offset=1))),series)
    assert result.signals==result.fills==()


def test_malformed_feature_dependency_metadata_rejected():
    series = list(bars((90,110)))
    spec = strategy(features=(reference(),))
    features = observations(spec,series)
    bad = features[0].model_copy(update={"input_start":series[-1].start_time+MINUTE/2})
    with pytest.raises(BacktestInputError,match="input_start"): simulate(spec,series,(bad,))
    delayed = series[0].model_copy(update={"available_at":START+timedelta(days=1)})
    with pytest.raises(BacktestInputError,match="contributing bar"):
        simulate(spec,(delayed,series[1]),features)


def test_aliased_sma_strategy_consumes_registry_outputs():
    series = bars((100,100,120,90))
    r = rule(C.CROSSES_ABOVE,left=FeatureOperand(feature_id="fast"),right=FeatureOperand(feature_id="slow"))
    spec = strategy(entry=group(r),no_exit=True,features=(reference(period=1),reference("slow",2)))
    result = simulate(spec,series,observations(spec,series))
    assert result.signals[0].source_bar_start==series[2].start_time
    assert result.fills[0].execution_price==series[3].open
    assert result.open_position is not None


def test_pending_fill_does_not_consume_execution_bar_close_availability():
    series = list(bars((101,99),opens=(50,100)))
    series[1] = series[1].model_copy(update={"available_at":START+timedelta(days=1)})
    result = simulate(series=series)
    assert len(result.fills)==1 and len(result.signals)==1
    assert result.fills[0].execution_time==series[1].start_time
    assert result.open_position is not None  # delayed close cannot signal exit
    assert result.unrealized_pnl==D("-2")  # retrospective mark only


def test_feature_observation_wrong_timestamp_is_missing_not_forward_filled():
    series = bars((110,))
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast"))),features=(reference(period=1),))
    observation, = observations(spec,series)
    shifted = observation.model_copy(update={"timestamp":observation.timestamp+MINUTE/2,
        "available_at":observation.available_at+MINUTE/2})
    assert simulate(spec,series,(shifted,)).signals==()


def test_shortened_sma_input_start_cannot_hide_a_delayed_dependency():
    series = list(bars((110, 110, 110)))
    series[0] = series[0].model_copy(update={"available_at":START+timedelta(days=1)})
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="fast_sma"))),
        no_exit=True, features=(reference("fast_sma", period=2),))
    observation = observations(spec, series)[0]
    assert observation.input_start == series[0].start_time
    assert observation.available_at == series[0].available_at
    assert simulate(spec, series, (observation,)).signals == ()

    premature = observation.model_copy(update={"available_at":series[1].end_time})
    with pytest.raises(BacktestInputError, match="contributing bar"):
        simulate(spec, series, (premature,))

    shortened = premature.model_copy(update={"input_start":series[1].start_time})
    with pytest.raises(BacktestInputError, match="input_start"):
        leaked = simulate(spec, series, (shortened,))
        # Before the fix this malformed observation produces a premature entry/fill.
        assert leaked.signals[0].signal_time == series[1].end_time
        assert leaked.fills[0].execution_time == series[2].start_time
        assert leaked.fills[0].action is A.ENTER_LONG


def test_backtest_values_independent_of_decimal_default_context():
    from quantlab.backtesting import ExecutionCostConfig

    series = bars((101, 110))
    spec = strategy(no_exit=True)
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(commission_per_unit=D("1e-40")))
    saved = DefaultContext.copy()
    caller = getcontext()
    before = (str(caller), caller.traps.copy(), caller.flags.copy())
    try:
        expected = simulate(spec, series, config=config)
        assert expected.fills[0].costs.commission == D("2e-40")
        DefaultContext.prec = 2
        DefaultContext.rounding = ROUND_DOWN
        DefaultContext.Emin = -2
        DefaultContext.Emax = 6
        DefaultContext.clamp = 1
        DefaultContext.capitals = 0
        for signal in DefaultContext.traps:
            DefaultContext.traps[signal] = False
            DefaultContext.flags[signal] = True
        actual = simulate(spec, series, config=config)
        assert actual.fills[0].costs.commission == D("2e-40")
        assert actual == expected
        assert (str(caller), caller.traps.copy(), caller.flags.copy()) == before
    finally:
        for field in ("prec", "rounding", "Emin", "Emax", "clamp", "capitals"):
            setattr(DefaultContext, field, getattr(saved, field))
        DefaultContext.traps.update(saved.traps)
        DefaultContext.flags.update(saved.flags)


@pytest.mark.parametrize("implementation,parameter,start", [
    ("open", None, 5), ("high", None, 5), ("low", None, 5), ("close", None, 5),
    ("simple_return", None, 4), ("log_return", None, 4),
    ("sma", "period", 4), ("ema", "period", 0), ("rsi", "period", 0),
    ("rolling_volatility", "window", 3),
])
def test_builtin_provenance_checks_the_full_required_history(implementation, parameter, start):
    declaration = FeatureReference(feature_id="alias", implementation_id=implementation,
        feature_type=FeatureType.INDICATOR, parameters=() if parameter is None else
        (FeatureArgument(name=parameter, value=2),))
    spec = strategy(entry=group(rule(left=FeatureOperand(feature_id="alias"))),
        features=(declaration,), no_exit=True)
    series = list(bars((110, 120, 130, 140, 150, 160)))
    series[start] = series[start].model_copy(update={"available_at":START+timedelta(days=1)})
    observation = observations(spec, series)[-1]
    assert observation.input_start == series[start].start_time
    assert simulate(spec, series, (observation,)).signals == ()
    premature = observation.model_copy(update={"available_at":observation.timestamp})
    with pytest.raises(BacktestInputError, match="contributing bar"):
        simulate(spec, series, (premature,))
    shortened = observation.model_copy(update={"input_start":series[start].start_time+MINUTE/2})
    with pytest.raises(BacktestInputError, match="input_start"):
        simulate(spec, series, (shortened,))
    # Computed history preceding a replay segment remains supported.
    assert simulate(spec, series[-1:], (observation,)).signals == ()


@pytest.mark.parametrize("implementation,parameter", [
    ("simple_return", None), ("log_return", None), ("sma", "period"),
    ("ema", "period"), ("rsi", "period"), ("rolling_volatility", "window"),
])
def test_builtin_provenance_rejects_claimed_history_inside_warmup(implementation, parameter):
    declaration = FeatureReference(feature_id="alias", implementation_id=implementation,
        feature_type=FeatureType.INDICATOR, parameters=() if parameter is None else
        (FeatureArgument(name=parameter, value=2),))
    spec = strategy(features=(declaration,))
    series = bars((110, 120, 130))
    observation = observations(spec, series)[0].model_copy(update={
        "timestamp":series[0].end_time, "input_start":series[0].start_time})
    with pytest.raises(BacktestInputError, match="input_start"):
        simulate(spec, series, (observation,))
