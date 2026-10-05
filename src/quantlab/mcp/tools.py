"""Thin MCP tool adapters over existing Quant Lab public services."""

from typing import Annotated, Any

from pydantic import Field, ValidationError

from quantlab import analytics, backtesting, data, features, ml, risk, validation
from quantlab.strategies import StrategyContent

from .decoding import decode_request, encode_json_object, service_issue
from .models import (
    BacktestExecutionRequest, BacktestExecutionResult,
    EntryRiskRequest, EntryRiskResult,
    FeatureComputationRequest, FeatureComputationResult,
    MarketDataResampleRequest, MarketDataResampleResult,
    MarketDataValidationRequest, MarketDataValidationResult,
    PerformanceAnalysisRequest, PerformanceAnalysisResult,
    StrategyValidationIssue, StrategyValidationResult,
)
from .research_models import (
    DatasetAdapterResult, DatasetRequest, HoldoutAdapterResult, HoldoutRequest,
    PredictionAdapterResult, PredictionFeaturesAdapterResult, PredictionFeaturesRequest,
    PredictionRequest, RobustnessAdapterResult, RobustnessRequest, TrainingAdapterResult,
    TrainingRequest, WalkForwardAdapterResult, WalkForwardRequest,
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


def run_backtest(request: dict[str, Any]) -> BacktestExecutionResult:
    """Stateless replay of a supplied approved specification; never grant approval.

    The deterministic public service owns signals, fills, costs, risk and P&L.
    Success means replay completed, not profitability or suitability for trading.
    """
    decoded, issues = decode_request(request, BacktestExecutionRequest)
    if decoded is None:
        return BacktestExecutionResult(success=False, issues=issues)
    try:
        value = backtesting.run_backtest(
            decoded.strategy, decoded.bars, decoded.features,
            instrument=decoded.instrument, config=decoded.config,
        )
    except backtesting.BacktestError:
        return BacktestExecutionResult(success=False, issues=service_issue())
    return BacktestExecutionResult(success=True, value=value)


def run_holdout(request: dict[str, Any]) -> HoldoutAdapterResult:
    """Independent chronological in-sample/OOS replays of approved intent."""
    decoded, issues = decode_request(request, HoldoutRequest)
    if decoded is None:
        return HoldoutAdapterResult(success=False, issues=issues)
    try:
        value = validation.run_holdout(decoded.strategy, decoded.bars, decoded.features,
            instrument=decoded.instrument, config=decoded.config, split=decoded.split,
            analytics_config=decoded.analytics_config)
    except validation.ResearchValidationError:
        return HoldoutAdapterResult(success=False, issues=service_issue())
    return HoldoutAdapterResult(success=True, value=value)


def run_walk_forward(request: dict[str, Any]) -> WalkForwardAdapterResult:
    """Bounded fixed-intent chronological folds; no fitting or winner selection."""
    decoded, issues = decode_request(request, WalkForwardRequest)
    if decoded is None:
        return WalkForwardAdapterResult(success=False, issues=issues)
    try:
        value = validation.run_walk_forward(decoded.strategy, decoded.bars, decoded.features,
            instrument=decoded.instrument, config=decoded.config, walk_forward=decoded.walk_forward,
            analytics_config=decoded.analytics_config)
    except validation.ResearchValidationError:
        return WalkForwardAdapterResult(success=False, issues=service_issue())
    return WalkForwardAdapterResult(success=True, value=value)


def run_parameter_robustness(request: dict[str, Any]) -> RobustnessAdapterResult:
    """Explicit separately approved structural variants, in supplied order."""
    decoded, issues = decode_request(request, RobustnessRequest)
    if decoded is None:
        return RobustnessAdapterResult(success=False, issues=issues)
    try:
        value = validation.run_parameter_robustness(decoded.candidates, decoded.bars,
            decoded.features, baseline_candidate_id=decoded.baseline_candidate_id,
            window=decoded.window, instrument=decoded.instrument, config=decoded.config,
            analytics_config=decoded.analytics_config)
    except validation.ResearchValidationError:
        return RobustnessAdapterResult(success=False, issues=service_issue())
    return RobustnessAdapterResult(success=True, value=value)


def build_ml_dataset(request: dict[str, Any]) -> DatasetAdapterResult:
    """Causal supervised joins using canonical bars/features and explicit windows."""
    decoded, issues = decode_request(request, DatasetRequest)
    if decoded is None:
        return DatasetAdapterResult(success=False, issues=issues)
    try:
        value = ml.build_dataset(decoded.bars, decoded.features, instrument=decoded.instrument,
            feature_schema=decoded.feature_schema, window=decoded.window, target=decoded.target)
    except ml.MLResearchError:
        return DatasetAdapterResult(success=False, issues=service_issue())
    return DatasetAdapterResult(success=True, value=value)


def train_ml_model(request: dict[str, Any]) -> TrainingAdapterResult:
    """Existing exact-rational ridge training; no estimator or executable inputs."""
    decoded, issues = decode_request(request, TrainingRequest)
    if decoded is None:
        return TrainingAdapterResult(success=False, issues=issues)
    try:
        value = ml.train_model(decoded.dataset, decoded.config)
    except ml.MLResearchError:
        return TrainingAdapterResult(success=False, issues=service_issue())
    return TrainingAdapterResult(success=True, value=value)


def predict_ml_oos(request: dict[str, Any]) -> PredictionAdapterResult:
    """Predictions strictly after the supplied artifact's information cutoff."""
    decoded, issues = decode_request(request, PredictionRequest)
    if decoded is None:
        return PredictionAdapterResult(success=False, issues=issues)
    try:
        value = ml.predict_oos(decoded.artifact, decoded.dataset)
    except ml.MLResearchError:
        return PredictionAdapterResult(success=False, issues=service_issue())
    return PredictionAdapterResult(success=True, value=value)


def ml_predictions_to_features(request: dict[str, Any]) -> PredictionFeaturesAdapterResult:
    """Canonical prediction features retaining model/cutoff/input provenance."""
    decoded, issues = decode_request(request, PredictionFeaturesRequest)
    if decoded is None:
        return PredictionFeaturesAdapterResult(success=False, issues=issues)
    try:
        value = ml.predictions_to_features(decoded.predictions, artifact=decoded.artifact,
            feature_id=decoded.feature_id)
    except ml.MLResearchError:
        return PredictionFeaturesAdapterResult(success=False, issues=service_issue())
    return PredictionFeaturesAdapterResult(success=True, value=value)
