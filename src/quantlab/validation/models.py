"""Strict immutable Phase 10 configurations and descriptive reports."""
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from quantlab.analytics import AnalyticsConfig, PerformanceReport
from quantlab.analytics.models import Count, NonPositiveDecimal
from quantlab.backtesting import BacktestConfig, BacktestResult
from quantlab.backtesting.models import FiniteDecimal
from quantlab.data.models import Identifier, NonNegativeDecimal, UtcTimestamp, _DomainModel
from quantlab.strategies import StrategySpecification


class ValidationWindow(_DomainModel):
    """Nonempty half-open indices into the supplied chronological bar sequence."""
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start >= self.end:
            raise ValueError("window start must precede end")
        return self


class HoldoutConfig(_DomainModel):
    train: ValidationWindow
    test: ValidationWindow

    @model_validator(mode="after")
    def separated(self) -> Self:
        if self.train.end > self.test.start:
            raise ValueError("train must precede test without overlap")
        return self


class WalkForwardMode(StrEnum):
    EXPANDING = "expanding"
    ROLLING = "rolling"


class WalkForwardConfig(_DomainModel):
    """Sizes and step are observed bar counts, never calendar durations."""
    train_size: Annotated[int, Field(gt=0)]
    test_size: Annotated[int, Field(gt=0)]
    step_size: Annotated[int, Field(gt=0)]
    mode: WalkForwardMode = WalkForwardMode.EXPANDING


class WindowMetadata(_DomainModel):
    window: ValidationWindow
    observation_count: Annotated[int, Field(gt=0)]
    first_decision_close: UtcTimestamp
    last_decision_close: UtcTimestamp

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.observation_count != self.window.end - self.window.start:
            raise ValueError("observation count must match window")
        if self.first_decision_close > self.last_decision_close:
            raise ValueError("decision closes must be chronological")
        if (self.observation_count == 1) != (self.first_decision_close == self.last_decision_close):
            raise ValueError("decision-close bounds must match observation count")
        return self


class WalkForwardFold(_DomainModel):
    fold_index: Count
    train: WindowMetadata
    test: WindowMetadata

    @model_validator(mode="after")
    def separated(self) -> Self:
        HoldoutConfig(train=self.train.window, test=self.test.window)
        if self.train.last_decision_close >= self.test.first_decision_close:
            raise ValueError("train decision closes must strictly precede test")
        return self


class SegmentResult(_DomainModel):
    metadata: WindowMetadata
    backtest: BacktestResult
    performance: PerformanceReport


class HoldoutResult(_DomainModel):
    definition_version: Literal["phase10-v1"] = "phase10-v1"
    config: HoldoutConfig
    backtest_config: BacktestConfig
    analytics_config: AnalyticsConfig
    in_sample: SegmentResult
    out_of_sample: SegmentResult


class WalkForwardFoldResult(_DomainModel):
    fold: WalkForwardFold
    in_sample: SegmentResult
    out_of_sample: SegmentResult


class DescriptiveSummary(_DomainModel):
    """Independent-run statistics; drawdowns retain Phase 9's negative sign."""
    evaluation_count: Annotated[int, Field(gt=0)]
    profitable_count: Count
    losing_count: Count
    breakeven_count: Count
    mean_total_return: FiniteDecimal
    median_total_return: FiniteDecimal
    minimum_total_return: FiniteDecimal
    maximum_total_return: FiniteDecimal
    total_return_range: NonNegativeDecimal
    closed_trade_count: Count
    mean_maximum_drawdown: NonPositiveDecimal
    minimum_maximum_drawdown: NonPositiveDecimal
    maximum_maximum_drawdown: NonPositiveDecimal

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.profitable_count + self.losing_count + self.breakeven_count != self.evaluation_count:
            raise ValueError("return class counts must match evaluation count")
        return self


class WalkForwardReport(_DomainModel):
    definition_version: Literal["phase10-v1"] = "phase10-v1"
    config: WalkForwardConfig
    backtest_config: BacktestConfig
    analytics_config: AnalyticsConfig
    folds: tuple[WalkForwardFoldResult, ...] = Field(min_length=1)
    out_of_sample_summary: DescriptiveSummary

    @property
    def fold_count(self) -> int:
        return len(self.folds)


class RobustnessCandidate(_DomainModel):
    """Caller-approved variant; approval is checked again at execution."""
    candidate_id: Identifier
    strategy: StrategySpecification


class RobustnessCandidateResult(_DomainModel):
    candidate: RobustnessCandidate
    evaluation: SegmentResult


class RobustnessReport(_DomainModel):
    definition_version: Literal["phase10-v1"] = "phase10-v1"
    baseline_candidate_id: Identifier
    metadata: WindowMetadata
    backtest_config: BacktestConfig
    analytics_config: AnalyticsConfig
    candidates: tuple[RobustnessCandidateResult, ...] = Field(min_length=1)
    summary: DescriptiveSummary

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)
