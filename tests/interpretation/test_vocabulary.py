import json

import pytest

from quantlab.features import DEFAULT_REGISTRY, validate_strategy_features
from quantlab.interpretation import InterpretedStrategyError
from quantlab.interpretation.vocabulary import INDICATORS, ML_IMPLEMENTATION
from quantlab.strategies import Comparison, Direction, GroupMode
from .helpers import TEXT, ready, run, source


def test_catalogue_exactly_matches_repository_feature_definitions():
    assert INDICATORS == tuple((d.feature_id, d.parameter_name, d.parameter_minimum)
                              for d in DEFAULT_REGISTRY.definitions.values())


@pytest.mark.parametrize("implementation,argument,minimum", INDICATORS)
def test_each_registered_indicator_proposal_passes_existing_compatibility(implementation, argument, minimum):
    wire = ready()
    feature = wire["draft"]["features"][0]
    feature["implementation_id"] = implementation
    feature["parameters"] = [] if argument is None else [dict(name=argument, value=minimum)]
    idea = f"Use {implementation} with {argument}={minimum}. " + TEXT
    wire["evidence"][-2]["quote"] = idea
    result, _ = run(wire, input=source(strategy_text=idea))
    requests = validate_strategy_features(result.proposal)
    assert requests[0].implementation_id == implementation


@pytest.mark.parametrize("changes", [
    {"implementation_id": "atr"}, {"implementation_id": None}, {"feature_type": "level"},
    {"timeframe": "5m"}, {"parameters": []},
    {"parameters": [{"name": "period", "value": 0}]},
    {"parameters": [{"name": "period", "value": True}]},
    {"parameters": [{"name": "period", "value": "2"}]},
    {"parameters": [{"name": "window", "value": 2}]},
    {"parameters": [{"name": "period", "value": 2}, {"name": "extra", "value": 1}]},
    {"implementation_id": "rolling_volatility", "parameters": [{"name": "window", "value": 1}]},
    {"implementation_id": "close", "parameters": [{"name": "period", "value": 2}]},
])
def test_unsupported_indicator_declarations_rejected(changes):
    wire = ready()
    wire["draft"]["features"][0].update(changes)
    with pytest.raises(InterpretedStrategyError):
        run(wire)


def test_legacy_feature_id_fallback_matches_repository():
    wire = ready()
    wire["draft"]["features"][0].update(feature_id="sma", implementation_id=None)
    wire["draft"]["long"]["entry"]["rules"][0]["right"]["feature_id"] = "sma"
    result, _ = run(wire)
    assert validate_strategy_features(result.proposal)[0].feature_id == "sma"


@pytest.mark.parametrize("digest", [0, 123456789, 2**256 - 1])
def test_bounded_ml_signal_is_declaration_only(digest):
    wire = ready()
    wire["draft"]["features"][0].update(implementation_id=ML_IMPLEMENTATION,
        feature_type="ml_signal", parameters=[dict(name="model_digest", value=digest)])
    idea = f"Use existing {ML_IMPLEMENTATION} with integer model_digest {digest}. " + TEXT
    wire["evidence"][-2]["quote"] = idea
    result, _ = run(wire, input=source(strategy_text=idea))
    request = validate_strategy_features(result.proposal)[0]
    assert request.implementation_id == ML_IMPLEMENTATION
    assert request.parameters[0].value == digest


@pytest.mark.parametrize("implementation,parameters", [
    ("unknown_model", [{"name": "model_digest", "value": 0}]),
    (None, [{"name": "model_digest", "value": 0}]),
    (ML_IMPLEMENTATION, []),
    (ML_IMPLEMENTATION, [{"name": "model_digest", "value": -1}]),
    (ML_IMPLEMENTATION, [{"name": "model_digest", "value": 2**256}]),
    (ML_IMPLEMENTATION, [{"name": "model_digest", "value": True}]),
    (ML_IMPLEMENTATION, [{"name": "model_digest", "value": "1"}]),
    (ML_IMPLEMENTATION, [{"name": "period", "value": 1}]),
])
def test_ml_identity_cannot_be_guessed_or_widened(implementation, parameters):
    wire = ready()
    wire["draft"]["features"][0].update(implementation_id=implementation,
        feature_type="ml_signal", parameters=parameters)
    with pytest.raises(InterpretedStrategyError):
        run(wire)


@pytest.mark.parametrize("direction", list(Direction))
@pytest.mark.parametrize("mode", list(GroupMode))
def test_existing_side_and_group_types_preserved(direction, mode):
    wire = ready()
    draft = wire["draft"]
    draft["long"]["entry"]["mode"] = mode.value
    draft["direction"] = direction.value
    if direction is not Direction.LONG:
        draft["short"] = json.loads(json.dumps(draft["long"]))
        wire["evidence"].append(dict(field="short", quote=TEXT))
    if direction is Direction.SHORT:
        draft["long"] = None
        wire["evidence"] = [e for e in wire["evidence"] if e["field"] != "long"]
    result, _ = run(wire)
    assert result.proposal.content.direction is direction


@pytest.mark.parametrize("operator", list(Comparison))
def test_exact_existing_operator_vocabulary(operator):
    wire = ready()
    wire["draft"]["long"]["entry"]["rules"][0]["comparison"] = operator.value
    result, _ = run(wire)
    assert result.proposal.content.long.entry.rules[0].comparison is operator


def test_phase5_optional_intent_is_preserved_without_claiming_execution_support():
    wire = ready()
    wire["draft"].update(stop_loss=dict(kind="fixed", value="2", unit="percent"),
        take_profit=dict(kind="risk_reward", multiple="3"), sizing_reference="size:external",
        session=dict(start_utc="08:00:00", end_utc="16:00:00", weekdays=[0, 1], overnight=False))
    idea = TEXT + " Stop 2 percent, target 3 times risk, sizing size:external, Mon/Tue 08:00-16:00 UTC."
    wire["evidence"].extend(dict(field=f, quote=idea) for f in
                            ("stop_loss", "take_profit", "sizing_reference", "session"))
    result, _ = run(wire, input=source(strategy_text=idea))
    assert result.proposal.content.stop_loss.value == 2
    assert result.proposal.content.session.weekdays == (0, 1)


def test_explicit_no_exit_remains_phase5_no_exit():
    wire = ready()
    wire["draft"]["long"]["exit"] = None
    idea = TEXT.replace("Exit when close is below threshold.", "Deliberately use no exit rule.")
    for evidence in wire["evidence"]:
        evidence["quote"] = idea
    result, _ = run(wire, input=source(strategy_text=idea))
    assert result.proposal.content.long.exit is None
