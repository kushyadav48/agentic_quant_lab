"""Input validation, causal dependencies, identities, and structural integration."""
from datetime import timedelta, timezone
import socket
import subprocess
import sys
import pytest
from pydantic import ValidationError
from quantlab.data import PriceType, Timeframe, MarketQuote
from quantlab.features import (DEFAULT_REGISTRY, FeatureObservation, FeatureRequest,
    FeatureParameter, validate_strategy_features)
from quantlab.strategies import (FeatureReference, FeatureArgument, FeatureType,
    StrategySpecification, StrategyContent, Direction, SideRules, RuleGroup, Rule,
    MarketOperand, MarketField, Comparison, ConstantOperand, Provenance, Origin)
from .helpers import START, MINUTE, INSTRUMENT, D, bars, compute, request

ALL = (request("open"), request("high"), request("low"), request("close"),
       request("simple_return"), request("log_return"), request("sma",3),
       request("ema",3), request("rsi",2), request("rolling_volatility",2))


@pytest.mark.parametrize("feature", ALL)
def test_each_prefix_matches_full_history(feature):
    series = bars((2,4,3,6,5,10))
    complete = compute(series, feature)
    for length in range(1,len(series)+1):
        assert compute(series[:length], feature) == tuple(o for o in complete if o.timestamp <= series[length-1].end_time)


@pytest.mark.parametrize("feature", ALL)
def test_delayed_availability_covers_all_dependencies(feature):
    series = list(bars((2,4,3,6,5,10)))
    series[1] = series[1].model_copy(update={"available_at": START+timedelta(days=1)})
    for observation in compute(series, feature):
        contributing = [b for b in series if observation.input_start <= b.start_time and b.end_time <= observation.timestamp]
        assert observation.available_at == max(b.available_at for b in contributing)
        assert observation.available_at >= observation.timestamp


@pytest.mark.parametrize("name", ["ema", "rsi"])
def test_recursive_history_delay_does_not_expire(name):
    series = list(bars((2,4,3,6,5,10)))
    series[0] = series[0].model_copy(update={"available_at":START+timedelta(days=2)})
    assert compute(series, request(name,2))[-1].available_at == series[0].available_at


def test_sma_and_volatility_delays_expire_with_actual_window():
    series = list(bars())
    series[0] = series[0].model_copy(update={"available_at":START+timedelta(days=2)})
    assert compute(series,request("sma",2))[-1].available_at == series[-1].available_at
    assert compute(series,request("rolling_volatility",2))[-1].available_at == series[-1].available_at


@pytest.mark.parametrize("change", ["ordering","duplicate","instrument","timeframe","price_type","overlap","ohlc","naive","early_availability"])
def test_reject_bad_input(change):
    series = list(bars())
    if change == "ordering": series[1],series[2] = series[2],series[1]
    elif change == "duplicate": series[1] = series[0]
    else:
        updates = {"instrument":{"instrument_id":"other"}, "timeframe":{"timeframe":Timeframe.M5},
            "price_type":{"price_type":PriceType.ASK}, "overlap":{"start_time":START+MINUTE/2},
            "ohlc":{"high":D("0.5")}, "naive":{"start_time":START.replace(tzinfo=None)},
            "early_availability":{"available_at":START}}[change]
        series[1] = series[1].model_copy(update=updates)
    with pytest.raises(ValueError): compute(series,request("close"))


def test_quotes_and_untyped_inputs_rejected():
    quote = MarketQuote(instrument_id=INSTRUMENT.instrument_id, source_id="fixture", dataset_id="fixture",
        timestamp=START,available_at=START,bid=D(1),ask=D(2))
    for candidate in (quote, bars()[0].model_dump()):
        with pytest.raises(ValueError): compute((candidate,),request("close"))


def test_gaps_and_mixed_provenance_are_explicitly_allowed():
    series = bars()
    other = series[-1].model_copy(update={"source_id":"another", "dataset_id":"other"})
    assert len(compute((series[0],other), request("simple_return"))) == 1


def test_empty_input_still_validates_requests():
    assert compute((),request("sma",3)) == ()
    with pytest.raises(ValueError): compute((),request("unknown"))
    with pytest.raises(ValueError): compute((),request("sma"))


def test_multiple_features_order_and_repeatability():
    series = bars()
    out = compute(series,*ALL)
    assert out == compute(series,*reversed(ALL)) == compute(series,*ALL)
    assert out == tuple(sorted(out,key=lambda o:(o.timestamp,o.feature_id)))
    for feature in ALL:
        assert tuple(o for o in out if o.feature_id==feature.feature_id) == compute(series,feature)


def test_aliases_keep_parameterized_feature_identities():
    a=FeatureRequest(feature_id="sma_2",implementation_id="sma",parameters=(FeatureParameter(name="period",value=2),))
    b=FeatureRequest(feature_id="sma_3",implementation_id="sma",parameters=(FeatureParameter(name="period",value=3),))
    out=compute(bars(),a,b)
    assert {o.feature_id for o in out} == {"sma_2","sma_3"}
    assert {o.implementation_id for o in out} == {"sma"}
    assert {o.parameters[0].value for o in out} == {2,3}
    with pytest.raises(ValueError): compute(bars(),request("sma",2),request("sma",3))


def test_registry_definitions_and_immutability():
    definition=DEFAULT_REGISTRY.get("rsi")
    assert definition.required_fields == ("close",)
    assert definition.required_bars(request("rsi",14)) == 15
    assert DEFAULT_REGISTRY.get("rolling_volatility").required_bars(request("rolling_volatility",5)) == 6
    with pytest.raises(TypeError): DEFAULT_REGISTRY.definitions["new"]=definition
    with pytest.raises(ValidationError): definition.feature_id="other"
    with pytest.raises(ValueError): DEFAULT_REGISTRY.get("not_registered")


def test_output_immutable_finite_and_json_roundtrip():
    observation, = compute(bars((1,)),request("close"))
    assert FeatureObservation.model_validate_json(observation.model_dump_json()) == observation
    with pytest.raises(ValidationError): observation.value=D(2)
    for updates in ({"value":D("NaN")},{"value":1.0},{"available_at":START},
                    {"timestamp":START.replace(tzinfo=None)}):
        with pytest.raises(ValueError): FeatureObservation.model_validate(observation.model_copy(update=updates))
    shifted=observation.model_copy(update={"timestamp":observation.timestamp.astimezone(timezone(timedelta(hours=5)))})
    assert FeatureObservation.model_validate(shifted).timestamp.tzinfo == timezone.utc


def reference(name="sma", n=3, **kwargs):
    return FeatureReference(feature_id=name,feature_type=FeatureType.INDICATOR,
        parameters=(FeatureArgument(name="period",value=n),),**kwargs)


def specification(feature):
    return StrategySpecification(strategy_id="fixture", version=1, content=StrategyContent(
        name="Compatibility only",instruments=(INSTRUMENT.instrument_id,),timeframe=Timeframe.M1,
        direction=Direction.LONG,long=SideRules(entry=RuleGroup(rules=(Rule(
            left=MarketOperand(field=MarketField.CLOSE), comparison=Comparison.GT,
            right=ConstantOperand(value=D(1))),))),features=(feature,),provenance=Provenance(origin=Origin.MANUAL)))


def test_strategy_supported_reference():
    spec=specification(reference(timeframe=Timeframe.M1))
    assert validate_strategy_features(spec) == (request("sma",3,timeframe=Timeframe.M1),)
    assert spec.state.value == "draft"


@pytest.mark.parametrize("feature", [reference("missing"),reference(n=0),reference(n=True),
    reference(n=D(3)),reference(timeframe=Timeframe.M5),
    FeatureReference(feature_id="sma",feature_type=FeatureType.ML_SIGNAL),
    FeatureReference(feature_id="sma_20",feature_type=FeatureType.INDICATOR,
        parameters=(FeatureArgument(name="period",value=20),)),
    FeatureReference(feature_id="sma",feature_type=FeatureType.INDICATOR,
        parameters=(FeatureArgument(name="window",value=3),))])
def test_incompatible_strategy_features(feature):
    with pytest.raises(ValueError): validate_strategy_features(specification(feature))


def test_pipeline_timeframe_override_rejected():
    with pytest.raises(ValueError): compute(bars(),request("sma",3,timeframe=Timeframe.M5))


def test_computation_no_network_or_input_mutation(monkeypatch):
    def deny(*args,**kwargs): raise AssertionError("network forbidden")
    monkeypatch.setattr(socket,"socket",deny)
    monkeypatch.setattr(socket,"create_connection",deny)
    series=bars()
    before=tuple(b.model_dump_json() for b in series)
    compute(series,*ALL)
    assert tuple(b.model_dump_json() for b in series)==before


def test_fresh_import_no_network():
    code="import socket, urllib.request; deny=lambda *a, **k: (_ for _ in ()).throw(AssertionError('network')); socket.socket=deny; socket.create_connection=deny; urllib.request.urlopen=deny; import quantlab.features"
    result=subprocess.run([sys.executable,"-B","-c",code],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_strategy_aliases_validate_and_preserve_distinct_outputs():
    fast = reference("fast_sma", n=2, implementation_id="sma")
    slow = reference("slow_sma", n=4, implementation_id="sma")
    base = specification(fast)
    content = StrategyContent.model_validate({**base.content.model_dump(mode="python"),
                                             "features": (fast, slow)})
    spec = base.revise(content)
    requests = validate_strategy_features(spec)
    assert tuple(r.feature_id for r in requests) == ("fast_sma", "slow_sma")
    assert tuple(r.implementation_id for r in requests) == ("sma", "sma")
    assert tuple(r.parameters[0].value for r in requests) == (2, 4)
    assert StrategySpecification.model_validate_json(spec.model_dump_json()) == spec
    output = compute(bars(), *requests)
    fast_output = tuple(o for o in output if o.feature_id == "fast_sma")
    slow_output = tuple(o for o in output if o.feature_id == "slow_sma")
    assert tuple(o.value for o in fast_output) == (D("1.5"), D("2.5"), D("3.5"), D("4.5"))
    assert tuple(o.value for o in slow_output) == (D("2.5"), D("3.5"))
    assert {o.implementation_id for o in output} == {"sma"}


def test_strategy_unknown_implementation_rejected_even_with_known_alias():
    spec = specification(reference("sma", implementation_id="missing_algorithm"))
    with pytest.raises(ValueError, match="unknown feature: missing_algorithm"):
        validate_strategy_features(spec)


def test_strategy_omitted_implementation_falls_back_to_feature_id():
    spec = specification(reference("sma", n=2))
    mapped, = validate_strategy_features(spec)
    assert mapped.feature_id == "sma" and mapped.implementation_id is None
    assert {o.implementation_id for o in compute(bars(), mapped)} == {"sma"}
