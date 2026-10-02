"""Hand-verifiable comparisons, exact offsets, crossings, and three-valued groups."""
import pytest
from pydantic import ValidationError

from quantlab.backtesting import EvaluationResult as E, RuleEvaluator, BacktestInputError
from quantlab.strategies import (
    Comparison as C, ConstantOperand, FeatureOperand, GroupMode, MarketField, MarketOperand,
    Parameter, ParameterOperand, ParameterType, Rule,
)
from .helpers import D, bars, group, observations, reference, rule, strategy


@pytest.mark.parametrize("comparison,a,b,expected", [
    (C.GT,101,100,E.TRUE), (C.GT,100,100,E.FALSE),
    (C.GE,100,100,E.TRUE), (C.GE,99,100,E.FALSE),
    (C.LT,99,100,E.TRUE), (C.LT,100,100,E.FALSE),
    (C.LE,100,100,E.TRUE), (C.LE,101,100,E.FALSE),
    (C.EQ,100,100,E.TRUE), (C.EQ,99,100,E.FALSE),
])
def test_numeric_comparisons(comparison,a,b,expected):
    r = rule(comparison, right=ConstantOperand(value=D(b)))
    assert RuleEvaluator(strategy(entry=group(r)), bars((a,))).evaluate(r,0) is expected


@pytest.mark.parametrize("field,value", [(MarketField.OPEN,101), (MarketField.HIGH,110),
    (MarketField.LOW,101), (MarketField.CLOSE,110)])
def test_real_market_fields(field,value):
    r = rule(C.EQ,left=MarketOperand(field=field),right=ConstantOperand(value=D(value)))
    assert RuleEvaluator(strategy(entry=group(r)),bars((110,),opens=(101,))).evaluate(r,0) is E.TRUE


def test_feature_alias_constant_and_default_parameter_resolution():
    series = bars((90,110,130))
    feature = FeatureOperand(feature_id="fast")
    parameter = ParameterOperand(name="threshold")
    r = rule(C.GE,left=parameter,right=feature)
    spec = strategy(entry=group(r),features=(reference(),),parameters=(
        Parameter(name="threshold",type=ParameterType.DECIMAL,default=D("110")),))
    evaluator = RuleEvaluator(spec,series,observations(spec,series))
    assert evaluator.evaluate(r,0) is E.UNAVAILABLE
    assert evaluator.evaluate(r,1) is E.TRUE  # default 110 >= SMA 100
    assert evaluator.evaluate(r,2) is E.FALSE  # default 110 < SMA 120
    assert evaluator.evaluate(rule(C.EQ,left=feature,right=ConstantOperand(value=D("120"))),2) is E.TRUE


@pytest.mark.parametrize("kind,value", [(ParameterType.INTEGER,100), (ParameterType.DECIMAL,D("100"))])
def test_numeric_parameter_defaults_keep_type(kind,value):
    r = rule(C.EQ,right=ParameterOperand(name="p"))
    spec = strategy(entry=group(r),parameters=(Parameter(name="p",type=kind,default=value),))
    assert type(spec.content.parameters[0].default) is type(value)
    assert RuleEvaluator(spec,bars((100,))).evaluate(r,0) is E.TRUE


@pytest.mark.parametrize("a,b,expected", [(True,True,E.TRUE),(True,False,E.FALSE),(False,False,E.TRUE)])
def test_boolean_equality_without_truthiness(a,b,expected):
    r = rule(C.EQ,left=ParameterOperand(name="enabled"),right=ConstantOperand(value=b))
    spec = strategy(entry=group(r),parameters=(Parameter(name="enabled",type=ParameterType.BOOLEAN,default=a),))
    assert RuleEvaluator(spec,bars((100,))).evaluate(r,0) is expected


@pytest.mark.parametrize("offset", [0,1,2,3,10000])
def test_market_offsets_and_missing_history(offset):
    r = rule(C.EQ,offset=offset,right=ConstantOperand(value=D("90")))
    evaluator = RuleEvaluator(strategy(entry=group(r)),bars((90,100,110)))
    expected = E.UNAVAILABLE if offset>2 else (E.TRUE if offset==2 else E.FALSE)
    assert evaluator.evaluate(r,2) is expected


def test_feature_offsets_are_exact_bar_timestamps_and_never_forward_filled():
    series = bars((90,110,130,150))
    r = rule(C.EQ,left=FeatureOperand(feature_id="fast",offset=1),right=ConstantOperand(value=D("100")))
    spec = strategy(entry=group(r),features=(reference(),))
    features = observations(spec,series)
    evaluator = RuleEvaluator(spec,series,features)
    assert evaluator.evaluate(r,1) is E.UNAVAILABLE  # previous bar warm-up
    assert evaluator.evaluate(r,2) is E.TRUE
    # The observation at N-1 is removed; an older available value cannot substitute.
    missing = tuple(o for o in features if o.timestamp!=series[2].end_time)
    assert RuleEvaluator(spec,series,missing).evaluate(r,3) is E.UNAVAILABLE


@pytest.mark.parametrize("mode", [GroupMode.ALL,GroupMode.ANY])
@pytest.mark.parametrize("a", list(E))
@pytest.mark.parametrize("b", list(E))
def test_group_truth_tables(mode,a,b):
    choices = {E.TRUE:rule(C.EQ,right=ConstantOperand(value=D("100"))),
        E.FALSE:rule(C.GT,right=ConstantOperand(value=D("100"))),
        E.UNAVAILABLE:rule(offset=1)}
    g = group(choices[a],choices[b],mode=mode)
    decisive = E.FALSE if mode is GroupMode.ALL else E.TRUE
    otherwise = E.TRUE if mode is GroupMode.ALL else E.FALSE
    expected = decisive if decisive in (a,b) else (E.UNAVAILABLE if E.UNAVAILABLE in (a,b) else otherwise)
    assert RuleEvaluator(strategy(entry=g),bars((100,))).evaluate(g,0) is expected


@pytest.mark.parametrize("comparison,prices,expected", [
    (C.CROSSES_ABOVE,(100,101),E.TRUE), (C.CROSSES_ABOVE,(99,101),E.TRUE),
    (C.CROSSES_ABOVE,(101,102),E.FALSE), (C.CROSSES_ABOVE,(99,100),E.FALSE),
    (C.CROSSES_BELOW,(100,99),E.TRUE), (C.CROSSES_BELOW,(101,99),E.TRUE),
    (C.CROSSES_BELOW,(99,98),E.FALSE), (C.CROSSES_BELOW,(101,100),E.FALSE),
])
def test_crossings_include_previous_equality(comparison,prices,expected):
    r = rule(comparison)
    evaluator = RuleEvaluator(strategy(entry=group(r)),bars(prices))
    assert evaluator.evaluate(r,0) is E.UNAVAILABLE
    assert evaluator.evaluate(r,1) is expected


@pytest.mark.parametrize("comparison,prices", [(C.CROSSES_ABOVE,(99,101,90)),(C.CROSSES_BELOW,(101,99,110))])
def test_crossings_preserve_operand_offsets(comparison,prices):
    r = rule(comparison,offset=1)
    evaluator = RuleEvaluator(strategy(entry=group(r)),bars(prices))
    assert evaluator.evaluate(r,1) is E.UNAVAILABLE
    assert evaluator.evaluate(r,2) is E.TRUE  # compares N-1 with N-2


def test_feature_vs_feature_crossing_and_missing_previous_observation():
    series = bars((100,100,120))
    r = rule(C.CROSSES_ABOVE,left=FeatureOperand(feature_id="fast"),right=FeatureOperand(feature_id="slow"))
    spec = strategy(entry=group(r),features=(reference(period=1),reference("slow",2)))
    features = observations(spec,series)
    evaluator = RuleEvaluator(spec,series,features)
    assert evaluator.evaluate(r,1) is E.UNAVAILABLE
    assert evaluator.evaluate(r,2) is E.TRUE  # fast:100->120; slow:100->110
    missing = tuple(o for o in features if not(o.feature_id=="slow" and o.timestamp==series[1].end_time))
    assert RuleEvaluator(spec,series,missing).evaluate(r,2) is E.UNAVAILABLE


def test_feature_crossing_offset():
    series = bars((100,100,120,90))
    r = rule(C.CROSSES_ABOVE,left=FeatureOperand(feature_id="fast",offset=1))
    spec = strategy(entry=group(r),features=(reference(period=1),))
    evaluator = RuleEvaluator(spec,series,observations(spec,series))
    assert evaluator.evaluate(r,3) is E.TRUE


@pytest.mark.parametrize("value", ['__import__("os").system("echo BAD")',object(),lambda:100,1.0])
def test_no_expression_objects_or_callbacks(value):
    with pytest.raises(ValidationError): ConstantOperand(value=value)


@pytest.mark.parametrize("index", [-1,1,True,0.0])
def test_invalid_evaluation_indices(index):
    with pytest.raises(BacktestInputError): RuleEvaluator(strategy(),bars((100,))).evaluate(rule(),index)
