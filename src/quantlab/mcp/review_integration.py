"""Trusted application handoff; deliberately absent from the MCP tool surface.

Retained accepted review -> separate exact-version approved specification ->
one backtest -> one performance analysis. No interpretation/resume/model call,
transport hop, revision, approval transition, or recursive dispatch occurs here.
"""
from pydantic import Field

from quantlab.orchestration import ResearchGraph, WorkflowStatus, get_workflow_snapshot
from quantlab.strategies import ApprovalState

from .models import BacktestExecutionResult
from .operation_models import (
    BacktestOperation, Key, OperationSnapshot, OperationState, OperationSubmission,
    PerformanceOperation, WorkflowBinding,
)
from .operations import OperationInputError, ResearchOperations, canonical_json, digest_json
from .research_models import ResearchReplayRequest


class ReviewedResearchRequest(ResearchReplayRequest):
    thread_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,127}\z")
    idempotency_key: Key = Field(repr=False)


class ReviewedResearchResult(WorkflowBinding):
    backtest: OperationSnapshot
    performance: OperationSnapshot | None = None


async def run_reviewed_backtest(graph: ResearchGraph, operations: ResearchOperations,
                                request: ReviewedResearchRequest) -> ReviewedResearchResult:
    """Run at most two permitted services, against actual retained human review.

    The caller is the trusted human application, not an MCP/model tool. The graph
    handle and operation namespace are application-owned capabilities. Phase 16
    acceptance remains a DRAFT proposal; this API never grants Phase 5 approval.
    """
    if type(operations) is not ResearchOperations:
        raise OperationInputError("invalid_review_binding")
    request = ReviewedResearchRequest.model_validate(request)
    snapshot = await get_workflow_snapshot(graph, thread_id=request.thread_id)
    strategy = request.strategy
    if (snapshot.status is not WorkflowStatus.ACCEPTED_FOR_APPROVAL
            or snapshot.review is None or strategy.state is not ApprovalState.APPROVED
            or (snapshot.review.thread_id, snapshot.review.strategy_id,
                snapshot.review.version, snapshot.review.content_digest) != (
                request.thread_id, strategy.strategy_id, strategy.version, strategy.content_digest)):
        raise OperationInputError("invalid_review_binding")
    binding = WorkflowBinding(thread_digest=digest_json(canonical_json(request.thread_id)),
        strategy_id=strategy.strategy_id, version=strategy.version,
        content_digest=strategy.content_digest)
    base = (request.idempotency_key, binding.model_dump(mode="python"))
    backtest_input = request.model_dump(mode="python", exclude={
        "thread_id", "idempotency_key", "analytics_config"})
    backtest = operations.submit(OperationSubmission(
        idempotency_key=digest_json(canonical_json((*base, "backtest"))),
        operation=BacktestOperation(kind="backtest", **backtest_input)), workflow=binding)
    backtest = operations.execute(backtest.operation_id)
    if backtest.state is not OperationState.COMPLETED:
        return ReviewedResearchResult(**binding.model_dump(mode="python"), backtest=backtest)
    result = BacktestExecutionResult.model_validate_json(backtest.result_json)
    performance = operations.submit(OperationSubmission(
        idempotency_key=digest_json(canonical_json((*base, "performance"))),
        operation=PerformanceOperation(kind="performance", result=result.value, config=request.analytics_config)),
        workflow=binding)
    performance = operations.execute(performance.operation_id)
    return ReviewedResearchResult(**binding.model_dump(mode="python"),
                                  backtest=backtest, performance=performance)
