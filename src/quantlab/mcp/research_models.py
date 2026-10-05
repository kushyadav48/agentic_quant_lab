"""Bounded Phase 10/12 requests; mathematical contracts remain service-owned."""
from typing import Self

from pydantic import Field, model_validator

from quantlab.analytics import AnalyticsConfig
from quantlab.backtesting import BacktestConfig
from quantlab.data import Instrument, MarketBar
from quantlab.features import FeatureObservation
from quantlab.ml import (
    ForwardReturnTarget, MLDataset, MLFeatureSchema, MLModelArtifact,
    MLModelConfig, MLPrediction,
)
from quantlab.validation import (
    HoldoutConfig, HoldoutResult, RobustnessCandidate, RobustnessReport,
    ValidationWindow, WalkForwardConfig, WalkForwardReport,
)

from .models import AdapterResult, BacktestExecutionRequest, MCPBoundaryModel
from .canonical import canonical_json


class ResearchRequest(MCPBoundaryModel):
    @model_validator(mode="after")
    def bounded(self) -> Self:
        schemas = [getattr(self, "feature_schema", None)]
        for name in ("dataset", "artifact"):
            value = getattr(self, name, None)
            if value is not None:
                schemas.append(value.feature_schema)
                if name == "dataset" and len(value.rows) > 5000:
                    raise ValueError("dataset exceeds admission bound")
        if any(schema is not None and len(schema.features) > 32 for schema in schemas):
            raise ValueError("feature width exceeds admission bound")
        canonical_json(self.model_dump(mode="python"), max_bytes=1_048_576)
        return self


class ResearchReplayRequest(BacktestExecutionRequest, ResearchRequest):
    bars: tuple[MarketBar, ...] = Field(max_length=5000)
    features: tuple[FeatureObservation, ...] = Field(default=(), max_length=100_000)
    analytics_config: AnalyticsConfig | None = None


class HoldoutRequest(ResearchReplayRequest):
    split: HoldoutConfig


class WalkForwardRequest(ResearchReplayRequest):
    walk_forward: WalkForwardConfig

    @model_validator(mode="after")
    def bounded_folds(self) -> Self:
        plan = self.walk_forward
        if max(0, (len(self.bars) - plan.train_size - plan.test_size) // plan.step_size + 1) > 64:
            raise ValueError("fold count exceeds admission bound")
        return self


class RobustnessRequest(ResearchRequest):
    candidates: tuple[RobustnessCandidate, ...] = Field(min_length=1, max_length=16)
    bars: tuple[MarketBar, ...] = Field(max_length=5000)
    features: tuple[FeatureObservation, ...] = Field(default=(), max_length=100_000)
    baseline_candidate_id: str = Field(min_length=1, max_length=128)
    window: ValidationWindow
    instrument: Instrument
    config: BacktestConfig
    analytics_config: AnalyticsConfig | None = None


class DatasetRequest(ResearchRequest):
    bars: tuple[MarketBar, ...] = Field(max_length=5000)
    features: tuple[FeatureObservation, ...] = Field(max_length=100_000)
    instrument: Instrument
    feature_schema: MLFeatureSchema
    window: ValidationWindow
    target: ForwardReturnTarget | None = None


class TrainingRequest(ResearchRequest):
    dataset: MLDataset
    config: MLModelConfig | None = None


class PredictionRequest(ResearchRequest):
    artifact: MLModelArtifact
    dataset: MLDataset


class PredictionFeaturesRequest(ResearchRequest):
    predictions: tuple[MLPrediction, ...] = Field(max_length=5000)
    artifact: MLModelArtifact
    feature_id: str = Field(min_length=1, max_length=128)


HoldoutAdapterResult = AdapterResult[HoldoutResult]
WalkForwardAdapterResult = AdapterResult[WalkForwardReport]
RobustnessAdapterResult = AdapterResult[RobustnessReport]
DatasetAdapterResult = AdapterResult[MLDataset]
TrainingAdapterResult = AdapterResult[MLModelArtifact]
PredictionAdapterResult = AdapterResult[tuple[MLPrediction, ...]]
PredictionFeaturesAdapterResult = AdapterResult[tuple[FeatureObservation, ...]]
