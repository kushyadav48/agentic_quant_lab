"""Finite operation identity, immutable snapshots and metadata-only audit."""
from enum import StrEnum
import hashlib
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from quantlab.data import MarketBar
from quantlab.features import FeatureObservation

from .models import (
    AdapterResult, BacktestExecutionRequest, MCPBoundaryModel, PerformanceAnalysisRequest,
)
from .research_models import (
    DatasetRequest, HoldoutRequest, PredictionFeaturesRequest, PredictionRequest,
    ResearchRequest, RobustnessRequest, TrainingRequest, WalkForwardRequest,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}\z", min_length=64, max_length=64)]
Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\z")]


class OperationKind(StrEnum):
    BACKTEST = "backtest"
    HOLDOUT = "holdout"
    WALK_FORWARD = "walk_forward"
    ROBUSTNESS = "robustness"
    DATASET = "ml_dataset"
    TRAINING = "ml_training"
    PREDICTION = "ml_prediction"
    PREDICTION_FEATURES = "ml_prediction_features"
    PERFORMANCE = "performance"


class OperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureClass(StrEnum):
    DOMAIN_REJECTION = "domain_rejection"
    INTERNAL_FAILURE = "internal_failure"


class BacktestOperation(BacktestExecutionRequest, ResearchRequest):
    bars: tuple[MarketBar, ...] = Field(max_length=5000)
    features: tuple[FeatureObservation, ...] = Field(default=(), max_length=100_000)
    kind: Literal["backtest"]


class HoldoutOperation(HoldoutRequest):
    kind: Literal["holdout"]


class WalkForwardOperation(WalkForwardRequest):
    kind: Literal["walk_forward"]


class RobustnessOperation(RobustnessRequest):
    kind: Literal["robustness"]


class DatasetOperation(DatasetRequest):
    kind: Literal["ml_dataset"]


class TrainingOperation(TrainingRequest):
    kind: Literal["ml_training"]


class PredictionOperation(PredictionRequest):
    kind: Literal["ml_prediction"]


class PredictionFeaturesOperation(PredictionFeaturesRequest):
    kind: Literal["ml_prediction_features"]


class PerformanceOperation(PerformanceAnalysisRequest, ResearchRequest):
    kind: Literal["performance"]


ResearchOperation = Annotated[
    BacktestOperation | HoldoutOperation | WalkForwardOperation | RobustnessOperation |
    DatasetOperation | TrainingOperation | PredictionOperation | PredictionFeaturesOperation |
    PerformanceOperation,
    Field(discriminator="kind"),
]


class OperationSubmission(MCPBoundaryModel):
    idempotency_key: Key = Field(repr=False)
    operation: ResearchOperation = Field(repr=False)


class OperationReference(MCPBoundaryModel):
    operation_id: Digest


class StrategyBinding(MCPBoundaryModel):
    strategy_id: str
    version: int = Field(ge=1)
    content_digest: Digest


class WorkflowBinding(StrategyBinding):
    """Trusted application context, never an MCP-submittable approval claim."""
    thread_digest: Digest


class AuditEvent(MCPBoundaryModel):
    sequence: int = Field(ge=1)
    timestamp: AwareDatetime
    operation_id: Digest
    kind: OperationKind
    request_digest: Digest
    idempotency_digest: Digest
    strategies: tuple[StrategyBinding, ...]
    workflow: WorkflowBinding | None = None
    previous_state: OperationState | None
    state: OperationState
    result_digest: Digest | None = None
    failure: FailureClass | None = None


class OperationSnapshot(MCPBoundaryModel):
    operation_id: Digest
    kind: OperationKind
    request_digest: Digest
    idempotency_digest: Digest
    strategies: tuple[StrategyBinding, ...]
    workflow: WorkflowBinding | None = None
    state: OperationState
    # Canonical JSON text keeps nested quantitative results immutable as well.
    # It contains a validated adapter result, never executable serialized objects.
    result_json: str | None = Field(default=None, repr=False)
    result_digest: Digest | None = None
    failure: FailureClass | None = None
    audit: tuple[AuditEvent, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.state is OperationState.COMPLETED:
            if self.result_json is None or self.result_digest is None or self.failure is not None:
                raise ValueError("completed operation requires a result")
            if hashlib.sha256(self.result_json.encode("utf-8")).hexdigest() != self.result_digest:
                raise ValueError("result digest must bind immutable result text")
        elif self.result_json is not None or self.result_digest is not None:
            raise ValueError("only completed operations carry results")
        if (self.state is OperationState.FAILED) != (self.failure is not None):
            raise ValueError("failure classification must match state")
        previous = None
        for index, event in enumerate(self.audit, 1):
            legal = (event.state is OperationState.QUEUED if previous is None else
                     event.state in (OperationState.RUNNING, OperationState.CANCELLED)
                     if previous is OperationState.QUEUED else
                     event.state in (OperationState.COMPLETED, OperationState.FAILED)
                     if previous is OperationState.RUNNING else False)
            if (not legal or event.previous_state is not previous or event.sequence != index
                    or any(getattr(event, name) != getattr(self, name) for name in (
                        "operation_id", "kind", "request_digest", "idempotency_digest", "strategies",
                        "workflow"))):
                raise ValueError("invalid audit chain")
            if ((event.state is OperationState.COMPLETED) != (event.result_digest is not None)
                    or (event.state is OperationState.FAILED) != (event.failure is not None)):
                raise ValueError("audit outcome must match transition")
            previous = event.state
        last = self.audit[-1]
        if (last.state is not self.state or last.result_digest != self.result_digest
                or last.failure != self.failure):
            raise ValueError("audit must describe the current state")
        return self


OperationAdapterResult = AdapterResult[OperationSnapshot]
