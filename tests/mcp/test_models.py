"""Tests for MCP-specific boundary models."""

import pytest
from pydantic import ValidationError

from quantlab.mcp.models import (
    MCPBoundaryModel, StrategyValidationIssue, StrategyValidationRequest,
    StrategyValidationResult,
)


class ExampleBoundaryModel(MCPBoundaryModel):
    value: str


def test_boundary_models_are_frozen():
    model = ExampleBoundaryModel(value="safe")

    with pytest.raises(ValidationError):
        model.value = "changed"


def test_boundary_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        ExampleBoundaryModel(value="safe", unexpected=True)


@pytest.mark.parametrize("value", [1, True, b"safe"])
def test_boundary_models_are_strict(value):
    with pytest.raises(ValidationError):
        ExampleBoundaryModel(value=value)


def test_request_rejects_non_json_and_non_finite_values():
    with pytest.raises(ValidationError):
        StrategyValidationRequest(content={"bad": (1, 2)})
    with pytest.raises(ValidationError):
        StrategyValidationRequest(content={"bad": float("nan")})


@pytest.mark.parametrize("values", [
    {"valid": True},
    {"valid": True, "content_digest": "a" * 64,
     "issues": (StrategyValidationIssue(code="invalid", message="Invalid"),)},
    {"valid": False},
    {"valid": False, "content_digest": "a" * 64},
    {"valid": True, "content_digest": "a" * 64 + "\n"},
    {"valid": "true", "content_digest": "a" * 64},
])
def test_validation_result_rejects_inconsistent_or_non_strict_state(values):
    with pytest.raises(ValidationError):
        StrategyValidationResult(**values)


def test_boundary_result_json_roundtrip():
    result = StrategyValidationResult(valid=False, issues=(
        StrategyValidationIssue(location=("timeframe",), code="missing", message="Required field is missing."),
    ))
    assert StrategyValidationResult.model_validate_json(result.model_dump_json()) == result
