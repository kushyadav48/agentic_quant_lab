"""Behavioral contract coverage using entirely offline strategy fixtures."""
from datetime import datetime, time, timezone
from decimal import Decimal, localcontext
import subprocess
import sys
import socket
import pytest
from pydantic import ValidationError
from quantlab.data import Timeframe
from quantlab.strategies import *

D = Decimal
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

def rule(**changes):
    values = dict(left=MarketOperand(field=MarketField.CLOSE), comparison=Comparison.GT,
                  right=ConstantOperand(value=D("1")))
    values.update(changes)
    return Rule(**values)

def content(**changes):
    values = dict(name="Manual baseline", instruments=("EURUSD",), timeframe=Timeframe.M5,
                  direction=Direction.LONG, long=SideRules(entry=RuleGroup(rules=(rule(),))),
                  provenance=Provenance(origin=Origin.MANUAL))
    values.update(changes)
    return StrategyContent(**values)

def spec(**changes):
    values = dict(strategy_id="baseline", version=1, content=content())
    values.update(changes)
    return StrategySpecification(**values)

def approval(s, **changes):
    values = dict(strategy_id=s.strategy_id, strategy_version=s.version,
                  content_digest=s.content_digest, reviewer="human:reviewer", reviewed_at=NOW)
    values.update(changes)
    return ApprovalRecord(**values)

def test_minimal_and_safe_timing():
    s = spec()
    assert s.state is ApprovalState.DRAFT
    assert s.content.timing.signal is SignalTiming.BAR_CLOSE
    assert s.content.timing.execution is ExecutionTiming.NEXT_BAR_OPEN
    assert len(s.content_digest) == 64

@pytest.mark.parametrize("target,field,value", [(spec(), "version", 2),
    (content(), "name", "edited"), (rule(), "comparison", Comparison.LT)])
def test_deep_immutability(target, field, value):
    with pytest.raises(ValidationError):
        setattr(target, field, value)
    assert isinstance(content().instruments, tuple)

@pytest.mark.parametrize("identifier", ["", " ", "a.b", "f()", "__import__", "a\nb"])
def test_bad_strategy_ids(identifier):
    with pytest.raises(ValidationError):
        spec(strategy_id=identifier)

@pytest.mark.parametrize("name", ["", " ", "\n"])
def test_blank_names(name):
    with pytest.raises(ValidationError):
        content(name=name)

def test_universe_and_empty_rules():
    with pytest.raises(ValidationError):
        content(instruments=())
    with pytest.raises(ValidationError):
        RuleGroup(rules=())
    with pytest.raises(ValidationError):
        content(instruments=("EURUSD", "EURUSD"))

@pytest.mark.parametrize("direction", list(Direction))
def test_direction(direction):
    side = content().long
    c = content(direction=direction, long=side if direction != Direction.SHORT else None,
                short=side if direction != Direction.LONG else None)
    assert c.direction is direction
    with pytest.raises(ValidationError):
        content(direction=direction, long=None, short=None)
    if direction != Direction.BOTH:
        with pytest.raises(ValidationError):
            content(direction=direction, long=side, short=side)

@pytest.mark.parametrize("mode", list(GroupMode))
def test_groups(mode):
    assert RuleGroup(mode=mode, rules=(rule(), rule(comparison=Comparison.LT))).mode is mode

@pytest.mark.parametrize("operator", ["!=", "eval", "gt", "close > sma_20"])
def test_unsupported_or_non_enum_python_operator(operator):
    with pytest.raises(ValidationError):
        rule(comparison=operator)

def test_code_and_unknown_fields_rejected():
    for value in ["close > 1", {"kind": "code", "expression": "exec('x')"},
                  {"kind": "feature", "feature_id": "os.system(x)"}]:
        with pytest.raises(ValidationError):
            rule(left=value)
    with pytest.raises(ValidationError):
        RuleGroup(rules=(rule(),), expression="eval(x)")
    with pytest.raises(ValidationError):
        spec(network=True)
    with pytest.raises(ValidationError):
        TimingIntent(execution="same_bar_close")

def test_features_and_stop_feature_references():
    f = FeatureReference(feature_id="sma_20", feature_type=FeatureType.INDICATOR,
                         parameters=(FeatureArgument(name="period", value=20),))
    side = SideRules(entry=RuleGroup(rules=(rule(right=FeatureOperand(feature_id="sma_20")),)))
    assert content(features=(f,), long=side).features == (f,)
    with pytest.raises(ValidationError):
        content(long=side)
    with pytest.raises(ValidationError):
        content(features=(f,f))
    with pytest.raises(ValidationError):
        content(stop_loss=FeatureDistance(feature_id="missing"))
    assert content(features=(f,), stop_loss=FeatureDistance(feature_id="sma_20"))
    with pytest.raises(ValidationError):
        FeatureReference(feature_id="x", feature_type=FeatureType.LEVEL,
                         parameters=(FeatureArgument(name="p", value=1),)*2)
    with pytest.raises(ValidationError):
        FeatureArgument(name="p", value={"code": "x"})

def test_parameter_references():
    p = Parameter(name="threshold", type=ParameterType.DECIMAL, default=D("1"))
    side = SideRules(entry=RuleGroup(rules=(rule(right=ParameterOperand(name=p.name)),)))
    assert content(parameters=(p,), long=side)
    with pytest.raises(ValidationError):
        content(long=side)
    with pytest.raises(ValidationError):
        content(parameters=(p,p))

@pytest.mark.parametrize("kind,default,lo,hi", [
    (ParameterType.INTEGER, 5, 1, 10), (ParameterType.DECIMAL, D("0.5"), D("0"), D("1")),
    (ParameterType.BOOLEAN, True, None, None)])
def test_parameter_types_and_bounds(kind, default, lo, hi):
    p = Parameter(name="p", type=kind, default=default, minimum=lo, maximum=hi)
    assert Parameter.model_validate_json(p.model_dump_json()) == p

@pytest.mark.parametrize("kind,default,lo,hi", [
    (ParameterType.INTEGER, True, None, None), (ParameterType.INTEGER, D("1"), None, None),
    (ParameterType.DECIMAL, 1, None, None), (ParameterType.DECIMAL, 1.0, None, None),
    (ParameterType.BOOLEAN, 1, None, None), (ParameterType.BOOLEAN, True, 0, None),
    (ParameterType.INTEGER, 1, 2, 3), (ParameterType.INTEGER, 4, 1, 3),
    (ParameterType.INTEGER, 2, 3, 1), (ParameterType.INTEGER, 2, True, 3),
    (ParameterType.DECIMAL, D("1"), D("2"), D("3")),
    (ParameterType.DECIMAL, D("4"), D("1"), D("3")),
    (ParameterType.DECIMAL, D("2"), D("3"), D("1")),
    (ParameterType.DECIMAL, D("NaN"), None, None)])
def test_bad_parameter(kind, default, lo, hi):
    with pytest.raises(ValidationError):
        Parameter(name="p", type=kind, default=default, minimum=lo, maximum=hi)

@pytest.mark.parametrize("field", ["stop_loss", "take_profit"])
@pytest.mark.parametrize("unit", list(DistanceUnit))
def test_stop_targets(field, unit):
    assert content(**{field: FixedDistance(value=D("2"), unit=unit)})
    for v in [D("0"), D("-1"), D("Infinity")]:
        with pytest.raises(ValidationError):
            content(**{field: FixedDistance(value=v, unit=unit)})
    if unit is DistanceUnit.PERCENT:
        with pytest.raises(ValidationError):
            FixedDistance(value=D("101"), unit=unit)

def test_risk_reward_and_boolean_semantics():
    with pytest.raises(ValidationError):
        content(take_profit=RiskRewardTarget(multiple=D("2")))
    assert content(stop_loss=FixedDistance(value=D("1"), unit=DistanceUnit.PRICE),
                   take_profit=RiskRewardTarget(multiple=D("2")))
    side = SideRules(entry=RuleGroup(rules=(rule(left=ConstantOperand(value=True),
        right=ConstantOperand(value=False), comparison=Comparison.EQ),)))
    assert content(long=side)
    with pytest.raises(ValidationError):
        content(long=SideRules(entry=RuleGroup(rules=(rule(right=ConstantOperand(value=True)),))))

@pytest.mark.parametrize("start,end,overnight", [(time(8), time(16), False), (time(22), time(6), True)])
def test_sessions(start,end,overnight):
    assert SessionFilter(start_utc=start,end_utc=end,weekdays=(0,1,4),overnight=overnight)

@pytest.mark.parametrize("changes", [dict(weekdays=()), dict(weekdays=(7,)),
    dict(weekdays=(-1,)), dict(weekdays=(True,)), dict(weekdays=(0,0)),
    dict(end_utc=time(8)), dict(end_utc=time(6)), dict(overnight=True),
    dict(start_utc=time(8,tzinfo=timezone.utc))])
def test_bad_sessions(changes):
    values=dict(start_utc=time(8),end_utc=time(16),weekdays=(0,))
    values.update(changes)
    with pytest.raises(ValidationError):
        SessionFilter(**values)

def test_digest_policy():
    s = spec()
    assert s.content_digest == spec(version=9, strategy_id="other", created_at=NOW).content_digest
    assert s.content_digest == s.mark_validated().approve(approval(s)).content_digest
    assert s.content_digest != spec(content=content(name="changed")).content_digest
    assert s.content_digest != spec(content=content(provenance=Provenance(origin=Origin.ML))).content_digest
    assert s.content_digest != spec(content=content(parameters=(Parameter(name="p",type=ParameterType.INTEGER,default=1),))).content_digest
    changed = content(long=SideRules(entry=RuleGroup(rules=(rule(comparison=Comparison.LT),))))
    assert changed.content_digest() != s.content_digest
    assert rule().right.value == D("1")
    same = content(long=SideRules(entry=RuleGroup(rules=(rule(right=ConstantOperand(value=D("1.00"))),))))
    assert same.content_digest() == s.content_digest
    with localcontext() as ctx:
        ctx.prec=2
        assert same.content_digest() == s.content_digest

@pytest.mark.parametrize("mode", list(GroupMode))
def test_digest_order_independence(mode):
    a, b = rule(), rule(comparison=Comparison.LT)
    args1 = (FeatureArgument(name="period", value=20),
             FeatureArgument(name="multiplier", value=D("2")))
    args2 = (FeatureArgument(name="period", value=10),
             FeatureArgument(name="multiplier", value=D("3")))
    f1 = FeatureReference(feature_id="a", feature_type=FeatureType.LEVEL, parameters=args1)
    f2 = FeatureReference(feature_id="b", feature_type=FeatureType.LEVEL, parameters=args2)
    reversed_f1 = FeatureReference(feature_id="a", feature_type=FeatureType.LEVEL,
                                   parameters=tuple(reversed(args1)))
    reversed_f2 = FeatureReference(feature_id="b", feature_type=FeatureType.LEVEL,
                                   parameters=tuple(reversed(args2)))
    p1 = Parameter(name="a", type=ParameterType.INTEGER, default=1)
    p2 = Parameter(name="b", type=ParameterType.INTEGER, default=2)
    side1 = SideRules(entry=RuleGroup(mode=mode, rules=(a, b)),
                      exit=RuleGroup(mode=mode, rules=(b, a)))
    side2 = SideRules(entry=RuleGroup(mode=mode, rules=(b, a)),
                      exit=RuleGroup(mode=mode, rules=(a, b)))
    c1 = content(instruments=("EURUSD", "USDJPY"), features=(f1, f2), parameters=(p1, p2),
                 direction=Direction.BOTH, long=side1, short=side2,
                 session=SessionFilter(start_utc=time(8), end_utc=time(16), weekdays=(0, 2, 4)))
    c2 = content(instruments=("USDJPY", "EURUSD"), features=(reversed_f2, reversed_f1),
                 parameters=(p2, p1), direction=Direction.BOTH, long=side2, short=side1,
                 session=SessionFilter(start_utc=time(8), end_utc=time(16), weekdays=(4, 0, 2)))
    assert c1.content_digest() == c2.content_digest()
    # Canonicalization must not mutate the declared tuple ordering.
    assert c1.features[0].parameters == args1
    assert c2.features[1].parameters == tuple(reversed(args1))

    changed_f1 = FeatureReference(feature_id="a", feature_type=FeatureType.LEVEL,
        parameters=(FeatureArgument(name="period", value=21), args1[1]))
    changed = StrategyContent.model_validate({**c2.model_dump(mode="python"),
                                              "features": (reversed_f2, changed_f1)})
    assert changed.content_digest() != c1.content_digest()

@pytest.mark.parametrize("changes", [dict(content_digest="0"*64),dict(strategy_id="other"),dict(strategy_version=2)])
def test_mismatched_approval(changes):
    s=spec().mark_validated()
    with pytest.raises(ValidationError):
        s.approve(approval(s,**changes))

def test_lifecycle_edit_and_roundtrip():
    s=spec()
    with pytest.raises(ValueError):
        s.approve(approval(s))
    approved=s.mark_validated().approve(approval(s))
    assert approved.state is ApprovalState.APPROVED
    assert StrategySpecification.model_validate_json(approved.model_dump_json()) == approved
    revised=approved.revise(content(name="Revised"))
    assert revised.version == 2 and revised.approval is None and revised.state is ApprovalState.DRAFT
    assert revised.content_digest != approved.content_digest
    with pytest.raises(ValidationError):
        revised.mark_validated().approve(approved.approval)
    with pytest.raises(ValidationError):
        spec(state=ApprovalState.APPROVED)
    with pytest.raises(ValidationError):
        spec(approval=approval(s))
    with pytest.raises(ValidationError):
        approval(s,reviewed_at=datetime(2026,1,1))

@pytest.mark.parametrize("origin", list(Origin))
def test_provenance(origin):
    s=spec(content=content(provenance=Provenance(origin=origin,source_reference="artifact:1",
        parent=ParentVersion(strategy_id="parent",version=1,content_digest="a"*64))))
    assert StrategySpecification.model_validate_json(s.model_dump_json()).content.provenance.origin is origin

def test_no_network(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError("network access forbidden")
    monkeypatch.setattr(socket,"socket",forbidden)
    monkeypatch.setattr(socket,"create_connection",forbidden)
    s=spec().mark_validated()
    approved=s.approve(approval(s))
    StrategySpecification.model_validate_json(approved.model_dump_json())
    approved.revise(content(name="offline"))
    validate_content(approved.content)


def test_fresh_import_has_no_network():
    code = (
        "import socket, urllib.request; "
        "deny=lambda *a, **k: (_ for _ in ()).throw(AssertionError('network')); "
        "socket.socket=deny; socket.create_connection=deny; urllib.request.urlopen=deny; "
        "import quantlab.strategies"
    )
    result = subprocess.run([sys.executable, "-B", "-c", code],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
