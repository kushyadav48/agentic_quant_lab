"""External JSON decoding without weakening deterministic domain contracts."""

from copy import deepcopy
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from quantlab.data import Timeframe
from quantlab.mcp.tools import validate_strategy_content
from quantlab.strategies import Direction, StrategyContent
from quantlab.strategies import validation

from .helpers import valid_strategy_content


def assert_invalid(result):
    assert result.valid is False
    assert result.content_digest is None
    assert result.issues


def test_valid_json_reaches_existing_validator_as_canonical_content(monkeypatch):
    seen = []
    original = validation.validate_content

    def observe(content):
        seen.append(content)
        original(content)

    monkeypatch.setattr(validation, "validate_content", observe)
    result = validate_strategy_content(valid_strategy_content())
    assert result.valid is True
    assert result.issues == ()
    assert len(result.content_digest) == 64
    assert len(seen) == 1
    strategy = seen[0]
    assert type(strategy) is StrategyContent
    assert strategy.direction is Direction.LONG
    assert strategy.timeframe is Timeframe.H1
    assert strategy.instruments == ("EURUSD",)
    assert isinstance(strategy.long.entry.rules, tuple)
    assert type(strategy.long.entry.rules[0].right.value) is Decimal
    assert strategy.long.entry.rules[0].right.value == Decimal("1")
    assert result.content_digest == strategy.content_digest()
    assert StrategyContent.model_validate(strategy) == strategy


def test_digest_matches_domain_and_is_stable_under_json_key_order():
    content = valid_strategy_content()
    expected = StrategyContent.model_validate_json(json.dumps(content)).content_digest()
    result = validate_strategy_content(content)
    assert result.content_digest == expected
    assert result == validate_strategy_content(dict(reversed(list(content.items()))))
    content["long"]["entry"]["rules"][0]["right"]["value"] = "1.00"
    assert validate_strategy_content(content).content_digest == expected


@pytest.mark.parametrize("field", ["name", "instruments", "timeframe", "direction", "provenance", "long"])
def test_missing_fields_are_rejected(field):
    content = valid_strategy_content()
    content.pop(field)
    result = validate_strategy_content(content)
    assert_invalid(result)
    if field != "long":
        assert any(issue.location == (field,) and issue.code == "missing" for issue in result.issues)
    else:
        assert any(issue.code == "value_error" for issue in result.issues)


@pytest.mark.parametrize("case", [
    "direction", "duplicate_instruments", "duplicate_features", "duplicate_parameters",
    "missing_feature", "missing_parameter", "boolean_comparison", "stop_feature",
    "target_feature", "risk_reward_without_stop",
])
def test_existing_semantic_rules_reject_invalid_content(case, monkeypatch):
    content = valid_strategy_content()
    rule = content["long"]["entry"]["rules"][0]
    if case == "direction":
        content["direction"] = "short"
    elif case == "duplicate_instruments":
        content["instruments"] *= 2
    elif case == "duplicate_features":
        content["features"] = [{"feature_id": "f", "feature_type": "level"}] * 2
    elif case == "duplicate_parameters":
        content["parameters"] = [{"name": "p", "type": "integer", "default": 1}] * 2
    elif case == "missing_feature":
        rule["left"] = {"kind": "feature", "feature_id": "missing"}
    elif case == "missing_parameter":
        rule["right"] = {"kind": "parameter", "name": "missing"}
    elif case == "boolean_comparison":
        rule["right"]["value"] = True
    elif case in ("stop_feature", "target_feature"):
        content["stop_loss" if case == "stop_feature" else "take_profit"] = {
            "kind": "feature", "feature_id": "missing",
        }
    else:
        content["take_profit"] = {"kind": "risk_reward", "multiple": "2"}

    seen = []
    original = validation.validate_content

    def observe(strategy):
        seen.append(strategy)
        original(strategy)

    monkeypatch.setattr(validation, "validate_content", observe)
    result = validate_strategy_content(content)
    assert_invalid(result)
    assert len(seen) == 1  # rejection comes from the Phase 5 validator
    assert any(issue.code == "value_error" for issue in result.issues)


@pytest.mark.parametrize("where", ["root", "operand", "provenance"])
def test_unexpected_fields_are_rejected_and_names_are_sanitized(where):
    content = valid_strategy_content()
    target = (content if where == "root" else content["provenance"] if where == "provenance"
              else content["long"]["entry"]["rules"][0]["left"])
    target["secret-extra-name"] = "secret-extra-value"
    result = validate_strategy_content(content)
    assert_invalid(result)
    assert any(issue.code == "extra_forbidden" for issue in result.issues)
    assert "secret-extra" not in result.model_dump_json()


@pytest.mark.parametrize("field", ["direction", "timeframe", "origin", "comparison", "market_field"])
def test_invalid_enum_values_are_rejected(field):
    content = valid_strategy_content()
    rule = content["long"]["entry"]["rules"][0]
    target, key = {
        "direction": (content, "direction"),
        "timeframe": (content, "timeframe"),
        "origin": (content["provenance"], "origin"),
        "comparison": (rule, "comparison"),
        "market_field": (rule["left"], "field"),
    }[field]
    target[key] = "secret-invalid-enum"
    result = validate_strategy_content(content)
    assert_invalid(result)
    assert any(issue.code == "enum" for issue in result.issues)
    assert "secret-invalid-enum" not in result.model_dump_json()


@pytest.mark.parametrize("offset", ["0", True, 1.0])
def test_json_mode_preserves_strict_integer_validation(offset):
    content = valid_strategy_content()
    content["long"]["entry"]["rules"][0]["left"]["offset"] = offset
    result = validate_strategy_content(content)
    assert_invalid(result)
    assert any(issue.code == "int_type" for issue in result.issues)


def test_core_python_strictness_remains_unchanged():
    raw = valid_strategy_content()
    assert validate_strategy_content(raw).valid
    with pytest.raises(ValidationError) as caught:
        StrategyContent.model_validate(raw)
    assert {"tuple_type", "is_instance_of"} <= {e["type"] for e in caught.value.errors()}
    canonical = StrategyContent.model_validate_json(json.dumps(raw))
    with pytest.raises(ValidationError):
        StrategyContent.model_validate({**canonical.model_dump(), "direction": "long"})
    with pytest.raises(ValidationError):
        StrategyContent.model_validate({**canonical.model_dump(), "instruments": ["EURUSD"]})


@pytest.mark.parametrize("valid", [True, False])
def test_adapter_does_not_mutate_input(valid):
    content = valid_strategy_content()
    if not valid:
        content["direction"] = "short"
    before = deepcopy(content)
    result = validate_strategy_content(content)
    assert result.valid is valid
    assert content == before


def test_validation_cannot_create_specification_or_approval(monkeypatch):
    from quantlab.strategies import ApprovalRecord, StrategySpecification

    def forbidden(*args, **kwargs):
        raise AssertionError("specification or approval construction forbidden")

    monkeypatch.setattr(StrategySpecification, "__init__", forbidden)
    monkeypatch.setattr(ApprovalRecord, "__init__", forbidden)
    result = validate_strategy_content(valid_strategy_content())
    assert result.valid
    assert set(result.model_dump()) == {"valid", "content_digest", "issues"}
    for field in ("approval", "approval_state", "state"):
        content = valid_strategy_content()
        content[field] = "approved"
        assert_invalid(validate_strategy_content(content))


@pytest.mark.parametrize("bad", [None, [], "{}", {1: "bad"}, {"bad": (1,)},
    {"bad": Decimal("1")}, {"bad": object()}, {"bad": float("nan")},
    {"bad": float("inf")}, {"bad": float("-inf")}])
def test_non_json_python_values_return_sanitized_failure(bad):
    result = validate_strategy_content(bad)
    assert_invalid(result)
    assert [issue.code for issue in result.issues] == ["invalid_json_content"]


def test_cyclic_input_returns_sanitized_failure():
    content = valid_strategy_content()
    content["cycle"] = content
    assert_invalid(validate_strategy_content(content))


def test_discriminator_error_does_not_echo_input():
    content = valid_strategy_content()
    content["long"]["entry"]["rules"][0]["left"]["kind"] = "secret-discriminator"
    result = validate_strategy_content(content)
    assert_invalid(result)
    assert any(issue.code == "union_tag_invalid" for issue in result.issues)
    assert "secret-discriminator" not in result.model_dump_json()


def test_semantic_exception_details_are_sanitized(monkeypatch):
    def fail(content):
        raise ValueError("secret internal detail C:/private/file.py")

    monkeypatch.setattr(validation, "validate_content", fail)
    result = validate_strategy_content(valid_strategy_content())
    assert_invalid(result)
    assert result.issues[0].message == "Strategy content violates domain constraints."
    assert "secret" not in result.model_dump_json()


def test_multiple_issues_are_deterministic_without_exception_metadata():
    content = valid_strategy_content()
    content.pop("name")
    content["timeframe"] = "secret-invalid"
    content["secret-key"] = "secret-value"
    first = validate_strategy_content(content)
    second = validate_strategy_content(dict(reversed(list(content.items()))))
    assert_invalid(first)
    assert first == second
    output = first.model_dump(mode="json")
    for issue in output["issues"]:
        assert set(issue) == {"location", "code", "message"}
    assert "secret" not in first.model_dump_json()
