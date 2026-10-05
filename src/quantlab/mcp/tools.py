"""Thin MCP tool adapters over existing Quant Lab public services."""

from typing import Annotated, Any

from pydantic import Field, ValidationError

from quantlab import analytics, data, features, risk
from quantlab.strategies import StrategyContent

from .decoding import decode_request, encode_json_object, service_issue
from .models import (
    EntryRiskRequest, EntryRiskResult,
    FeatureComputationRequest, FeatureComputationResult,
    MarketDataResampleRequest, MarketDataResampleResult,
    MarketDataValidationRequest, MarketDataValidationResult,
    PerformanceAnalysisRequest, PerformanceAnalysisResult,
    StrategyValidationIssue, StrategyValidationResult,
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
    content: Annotated[dict[str, Any], Field(description=(
        "Raw StrategyContent JSON object, not a StrategySpecification. "
        "Supply enum strings, arrays, and decimal strings as defined by the "
        "existing strategy contract; invalid content returns validation issues."
    ))],
) -> StrategyValidationResult:
    """Validate StrategyContent without approval, repair or strategy execution."""

    try:
        wire = encode_json_object(content)
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


def validate_market_data(request: dict[str, Any]) -> MarketDataValidationResult:
    """Query quality without sorting, repairing, or dropping observations."""
    decoded, issues = decode_request(request, MarketDataValidationRequest)
    if decoded is None:
        return MarketDataValidationResult(success=False, issues=issues)
    value = data.validate_dataset(
        decoded.observations, instrument=decoded.instrument,
        options=decoded.options, expected_starts=decoded.expected_starts,
    )
    return MarketDataValidationResult(success=True, value=value)


def resample_market_data(request: dict[str, Any]) -> MarketDataResampleResult:
    """Use the public Phase 4 service and its explicit ResampleRequest."""
    decoded, issues = decode_request(request, MarketDataResampleRequest)
    if decoded is None:
        return MarketDataResampleResult(success=False, issues=issues)
    try:
        value = data.resample(decoded.observations, decoded.request)
    except ValueError:
        return MarketDataResampleResult(success=False, issues=service_issue())
    return MarketDataResampleResult(success=True, value=value)


def compute_features(request: dict[str, Any]) -> FeatureComputationResult:
    """Use only the application's trusted default registry; no injection."""
    decoded, issues = decode_request(request, FeatureComputationRequest)
    if decoded is None:
        return FeatureComputationResult(success=False, issues=issues)
    try:
        value = features.compute_features(
            decoded.bars, decoded.requested_features, instrument=decoded.instrument,
        )
    except ValueError:
        return FeatureComputationResult(success=False, issues=service_issue())
    return FeatureComputationResult(success=True, value=value)


def evaluate_entry_risk(request: dict[str, Any]) -> EntryRiskResult:
    """Return the entry decision unchanged; success never implies ALLOW."""
    decoded, issues = decode_request(request, EntryRiskRequest)
    if decoded is None:
        return EntryRiskResult(success=False, issues=issues)
    try:
        value = risk.evaluate_entry_risk(decoded.context, decoded.config)
    except risk.RiskInputError:
        return EntryRiskResult(success=False, issues=service_issue())
    return EntryRiskResult(success=True, value=value)


def analyze_performance(request: dict[str, Any]) -> PerformanceAnalysisResult:
    """Analyze an existing result; never replay fills or alter accounting."""
    decoded, issues = decode_request(request, PerformanceAnalysisRequest)
    if decoded is None:
        return PerformanceAnalysisResult(success=False, issues=issues)
    try:
        value = analytics.analyze_performance(decoded.result, decoded.config)
    except analytics.AnalyticsInputError:
        return PerformanceAnalysisResult(success=False, issues=service_issue())
    return PerformanceAnalysisResult(success=True, value=value)
