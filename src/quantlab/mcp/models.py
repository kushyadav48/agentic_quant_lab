"""Phase 17 MCP-specific boundary models.

Domain models continue to live in their existing Quant Lab packages.
Only models genuinely specific to the MCP boundary belong here.
"""

from typing import Generic, Self, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from quantlab.analytics import AnalyticsConfig, PerformanceReport
from quantlab.backtesting import BacktestResult
from quantlab.data import (
    DataQualityReport, Instrument, MarketBar, MarketQuote, ResampleRequest,
    ValidationOptions,
)
from quantlab.features import FeatureObservation, FeatureRequest
from quantlab.risk import RiskConfig, RiskContext, RiskDecision


class MCPBoundaryModel(BaseModel):
    """Strict base model for MCP-only contracts."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )


class StrategyValidationRequest(MCPBoundaryModel):
    """Raw JSON-compatible strategy content submitted for validation."""

    content: dict[str, JsonValue]


class StrategyValidationIssue(MCPBoundaryModel):
    """Sanitized validation issue exposed at the MCP boundary."""

    location: tuple[str, ...] = ()
    code: str
    message: str


class StrategyValidationResult(MCPBoundaryModel):
    """Result of deterministic StrategyContent validation."""

    valid: bool
    content_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        min_length=64,
        max_length=64,
    )
    issues: tuple[StrategyValidationIssue, ...] = ()

    @model_validator(mode="after")
    def check_result(self) -> Self:
        if self.valid:
            if self.content_digest is None or self.issues:
                raise ValueError("valid content requires a digest and no issues")
        elif self.content_digest is not None or not self.issues:
            raise ValueError("invalid content requires issues and no digest")
        return self


class MarketDataValidationRequest(MCPBoundaryModel):
    observations: tuple[MarketQuote | MarketBar, ...]
    instrument: Instrument
    options: ValidationOptions | None = None
    expected_starts: tuple[AwareDatetime, ...] | None = None


class MarketDataResampleRequest(MCPBoundaryModel):
    observations: tuple[MarketQuote | MarketBar, ...]
    request: ResampleRequest


class FeatureComputationRequest(MCPBoundaryModel):
    bars: tuple[MarketBar, ...]
    requested_features: tuple[FeatureRequest, ...]
    instrument: Instrument


class EntryRiskRequest(MCPBoundaryModel):
    context: RiskContext
    config: RiskConfig


class PerformanceAnalysisRequest(MCPBoundaryModel):
    result: BacktestResult
    config: AnalyticsConfig | None = None


class AdapterIssue(MCPBoundaryModel):
    """Fixed messages and schema-only locations; never exception details."""

    location: tuple[str, ...] = ()
    code: str
    message: str


ResultValue = TypeVar("ResultValue")


class AdapterResult(MCPBoundaryModel, Generic[ResultValue]):
    """Success means the query completed, not dataset validity or risk ALLOW."""

    success: bool
    value: ResultValue | None = None
    issues: tuple[AdapterIssue, ...] = ()

    @model_validator(mode="after")
    def check_result(self) -> Self:
        if self.success:
            if self.value is None or self.issues:
                raise ValueError("success requires a value and no issues")
        elif self.value is not None or not self.issues:
            raise ValueError("failure requires issues and no value")
        return self


MarketDataValidationResult = AdapterResult[DataQualityReport]
MarketDataResampleResult = AdapterResult[tuple[MarketBar, ...]]
FeatureComputationResult = AdapterResult[tuple[FeatureObservation, ...]]
EntryRiskResult = AdapterResult[RiskDecision]
PerformanceAnalysisResult = AdapterResult[PerformanceReport]
