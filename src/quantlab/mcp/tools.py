"""Thin MCP tool adapters over existing Quant Lab public services."""

import json
from typing import Any

from pydantic import ValidationError

from quantlab.strategies import StrategyContent

from .models import (
    StrategyValidationIssue, StrategyValidationRequest, StrategyValidationResult,
)


# Never forward Pydantic messages: even without input/context, discriminator
# errors can embed submitted values and ValueError messages can contain details.
_ISSUE_MESSAGES = {
    "missing": "Required field is missing.",
    "extra_forbidden": "Unexpected field is not allowed.",
    "enum": "Unsupported enum value.",
    "union_tag_invalid": "Unsupported operand or distance kind.",
    "union_tag_not_found": "Operand or distance kind is required.",
    "value_error": "Strategy content violates domain constraints.",
}


def _invalid_json() -> StrategyValidationResult:
    return StrategyValidationResult(
        valid=False,
        issues=(StrategyValidationIssue(
            code="invalid_json_content",
            message="Content must be a JSON object containing only finite JSON values.",
        ),),
    )


def _validation_issues(error: ValidationError) -> tuple[StrategyValidationIssue, ...]:
    issues = []

    for item in error.errors(
        include_input=False,
        include_url=False,
        include_context=False,
    ):
        location = tuple(str(part) for part in item["loc"])
        code = item["type"]
        if code == "extra_forbidden":
            # Unknown field names are caller-controlled, unlike schema locations.
            location = (*location[:-1], "<extra>")

        issues.append(
            StrategyValidationIssue(
                location=location,
                code=code,
                message=_ISSUE_MESSAGES.get(code, "Invalid strategy content."),
            )
        )

    return tuple(sorted(issues, key=lambda issue: (issue.location, issue.code)))


def validate_strategy_content(
    content: dict[str, Any],
) -> StrategyValidationResult:
    """Decode external JSON into the strict domain contract; never repair it."""

    try:
        # Check wire types before encoding: json.dumps alone coerces tuple values
        # and non-string keys. No custom serializer or Python-object conversion.
        request = StrategyValidationRequest(content=content)
        wire = json.dumps(request.content, allow_nan=False, sort_keys=True)
    except (ValidationError, TypeError, ValueError, RecursionError):
        return _invalid_json()

    try:
        # JSON mode accepts wire enums/arrays/Decimals while preserving strict
        # integer/boolean types and all existing StrategyContent semantic checks.
        strategy = StrategyContent.model_validate_json(wire, strict=True)
    except ValidationError as exc:
        return StrategyValidationResult(
            valid=False,
            content_digest=None,
            issues=_validation_issues(exc),
        )

    return StrategyValidationResult(
        valid=True,
        content_digest=strategy.content_digest(),
        issues=(),
    )
