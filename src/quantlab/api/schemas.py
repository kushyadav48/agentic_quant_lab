"""HTTP envelopes around existing domain contracts, with no quant calculations."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from quantlab.paper.session_models import SessionSnapshot
from quantlab.journal import HistoryQuery
from quantlab.mcp.models import (
    StrategyValidationRequest, BacktestExecutionResult, PerformanceAnalysisResult,
)
from quantlab.mcp.research_models import (
    HoldoutAdapterResult, WalkForwardAdapterResult, RobustnessAdapterResult,
    DatasetAdapterResult, TrainingAdapterResult, PredictionAdapterResult,
    PredictionFeaturesAdapterResult,
)
from quantlab.mcp.operation_models import (
    AuditEvent, Digest, FailureClass, OperationKind, OperationSnapshot,
    OperationState, StrategyBinding, WorkflowBinding,
)


class HTTPModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class ValidationIssue(HTTPModel):
    location: tuple[str, ...] = ()
    code: Literal["contract_violation"] = "contract_violation"
    message: Literal["Input violates the API contract."] = "Input violates the API contract."


class ErrorResponse(HTTPModel):
    code: str
    message: str
    request_id: str
    issues: tuple[ValidationIssue, ...] = ()


class Health(HTTPModel):
    status: Literal["ok"] = "ok"
    api_version: Literal["v1"] = "v1"


class Capabilities(HTTPModel):
    api_version: Literal["v1"] = "v1"
    research_kinds: tuple[OperationKind, ...]
    execution: Literal["explicit_process_local"] = "explicit_process_local"
    maximum_operations: int
    maximum_request_bytes: int
    paper_sessions: tuple[str, ...]
    portfolios: tuple[str, ...]
    journal: bool
    unsupported: tuple[str, ...] = (
        "strategy_approval", "paper_commands", "paper_recovery", "live_execution",
        "background_workers", "durable_jobs", "llm_interpretation", "dataset_download",
        "journal_writes", "portfolio_mutations",
    )


class OperationStatus(HTTPModel):
    """Metadata-only projection; polling never copies the retained result."""
    operation_id: Digest
    kind: OperationKind
    request_digest: Digest
    idempotency_digest: Digest
    strategies: tuple[StrategyBinding, ...]
    workflow: WorkflowBinding | None
    state: OperationState
    result_digest: Digest | None
    failure: FailureClass | None
    audit: tuple[AuditEvent, ...]

    @classmethod
    def from_snapshot(cls, snapshot: OperationSnapshot):
        return cls(**{name: getattr(snapshot, name) for name in cls.model_fields})


class PaperReport(HTTPModel):
    snapshot: SessionSnapshot
    operator_required: bool = Field(description="Existing recovered-session operator gate; HTTP cannot clear it.")


class JournalQuery(HistoryQuery):
    """Tighter HTTP page budget; the journal still owns filtering and cursors."""
    limit: int = Field(default=10, ge=1, le=20)


class ResultBase(HTTPModel):
    operation_id: Digest
    result_digest: Digest
    result_json: str = Field(description="Exact retained canonical adapter JSON; SHA-256 binds these UTF-8 bytes.")


class BacktestResultResponse(ResultBase):
    kind: Literal["backtest"]
    result: BacktestExecutionResult


class HoldoutResultResponse(ResultBase):
    kind: Literal["holdout"]
    result: HoldoutAdapterResult


class WalkForwardResultResponse(ResultBase):
    kind: Literal["walk_forward"]
    result: WalkForwardAdapterResult


class RobustnessResultResponse(ResultBase):
    kind: Literal["robustness"]
    result: RobustnessAdapterResult


class DatasetResultResponse(ResultBase):
    kind: Literal["ml_dataset"]
    result: DatasetAdapterResult


class TrainingResultResponse(ResultBase):
    kind: Literal["ml_training"]
    result: TrainingAdapterResult


class PredictionResultResponse(ResultBase):
    kind: Literal["ml_prediction"]
    result: PredictionAdapterResult


class PredictionFeaturesResultResponse(ResultBase):
    kind: Literal["ml_prediction_features"]
    result: PredictionFeaturesAdapterResult


class PerformanceResultResponse(ResultBase):
    kind: Literal["performance"]
    result: PerformanceAnalysisResult


RESULT_MODELS = {model.model_fields["kind"].annotation.__args__[0]: model for model in (
    BacktestResultResponse, HoldoutResultResponse, WalkForwardResultResponse,
    RobustnessResultResponse, DatasetResultResponse, TrainingResultResponse,
    PredictionResultResponse, PredictionFeaturesResultResponse, PerformanceResultResponse,
)}

ResearchResult = Annotated[
    BacktestResultResponse | HoldoutResultResponse | WalkForwardResultResponse |
    RobustnessResultResponse | DatasetResultResponse | TrainingResultResponse |
    PredictionResultResponse | PredictionFeaturesResultResponse | PerformanceResultResponse,
    Field(discriminator="kind"),
]

# Re-export the established request contract rather than weakening its content.
StrategyRequest = StrategyValidationRequest
