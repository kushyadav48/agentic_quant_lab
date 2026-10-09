"""Versioned HTTP routing; execution and accounting remain application-owned."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from quantlab.journal import HistoryQuery, TradeHistoryPage, ResearchHistoryPage, SessionSummary
from quantlab.mcp import tools
from quantlab.mcp.models import StrategyValidationResult
from quantlab.mcp.operation_models import OperationSubmission, OperationState
from quantlab.paper.models import Identity
from quantlab.portfolio import PortfolioSnapshot

from .dependencies import body_contract, get_lane, request_schema, wire_response
from .errors import APIError
from .schemas import (
    Capabilities, Health, JournalQuery, OperationStatus, PaperReport, RESULT_MODELS, ResearchResult, StrategyRequest,
)
from .security import require_operator, require_reader, logger
import json

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_reader)])
OperationID = Annotated[str, Path(pattern="^[0-9a-f]{64}$", min_length=64, max_length=64)]


@router.get("/ready", response_model=Health)
async def readiness(request: Request):
    lane = get_lane(request)
    await lane.call(lane.services.check_ready)
    return Health()


@router.get("/capabilities", response_model=Capabilities)
async def capabilities(request: Request):
    lane = get_lane(request)
    return lane.services.capabilities(request.app.state.settings)


@router.post("/strategies/validate", response_model=StrategyValidationResult,
    openapi_extra=request_schema(StrategyRequest))
async def validate_strategy(request: Request,
    item: StrategyRequest = Depends(body_contract(StrategyRequest))):
    return await get_lane(request).call(lambda: wire_response(tools.validate_strategy_content(item.content)))


@router.post("/research/operations", response_model=OperationStatus, status_code=201,
    dependencies=[Depends(require_operator)], openapi_extra=request_schema(OperationSubmission))
async def submit_operation(request: Request,
    item: OperationSubmission = Depends(body_contract(OperationSubmission))):
    lane = get_lane(request)
    value = await lane.call(lambda: OperationStatus.from_snapshot(lane.services.operations.submit(item)))
    return value


@router.get("/research/operations/{operation_id}", response_model=OperationStatus)
async def operation_status(request: Request, operation_id: OperationID):
    # Existing lock protects a tiny metadata read even while quant execution runs.
    return OperationStatus.from_snapshot(get_lane(request).services.operations.get(operation_id))


@router.post("/research/operations/{operation_id}/execute", response_model=OperationStatus,
    dependencies=[Depends(require_operator)])
async def execute_operation(request: Request, operation_id: OperationID):
    lane = get_lane(request)
    value = await lane.call(lambda: OperationStatus.from_snapshot(lane.services.operations.execute(operation_id)))
    logger.info(json.dumps({"event": "research_outcome", "request_id": request.state.request_id,
        "operation_id": value.operation_id, "request_digest": value.request_digest,
        "result_digest": value.result_digest, "state": value.state}, separators=(",", ":")))
    return value


@router.post("/research/operations/{operation_id}/cancel", response_model=OperationStatus,
    dependencies=[Depends(require_operator)])
async def cancel_operation(request: Request, operation_id: OperationID):
    return OperationStatus.from_snapshot(get_lane(request).services.operations.cancel(operation_id))


@router.get("/research/operations/{operation_id}/result", response_model=ResearchResult)
async def operation_result(request: Request, operation_id: OperationID):
    lane = get_lane(request)
    def result():
        snapshot = lane.services.operations.get(operation_id)
        if snapshot.state is not OperationState.COMPLETED:
            raise APIError(409, "result_unavailable")
        model = RESULT_MODELS[snapshot.kind]
        adapter = model.model_fields["result"].annotation.model_validate_json(snapshot.result_json, strict=True)
        return wire_response(model(operation_id=snapshot.operation_id, kind=snapshot.kind.value,
            result_digest=snapshot.result_digest, result_json=snapshot.result_json, result=adapter))
    return await lane.call(result)


@router.get("/paper/sessions/{session_id}/snapshot", response_model=PaperReport)
async def paper_snapshot(request: Request, session_id: Identity):
    lane = get_lane(request)
    def report():
        owner = lane.services.paper_sessions.get(session_id)
        if owner is None:
            raise APIError(404, "not_found")
        # Public owner accessor enforces durable uncertain-commit recovery gates.
        return wire_response(PaperReport(snapshot=owner.snapshot,
            operator_required=getattr(owner, "operator_required", False)))
    return await lane.call(report)


@router.get("/portfolios/{portfolio_id}/snapshot", response_model=PortfolioSnapshot)
async def portfolio_snapshot(request: Request, portfolio_id: Identity):
    lane = get_lane(request)
    def report():
        owner = lane.services.portfolios.get(portfolio_id)
        if owner is None:
            raise APIError(404, "not_found")
        return wire_response(owner.snapshot)
    return await lane.call(report)


def journal_owner(lane):
    if lane.services.journal is None:
        raise APIError(503, "service_unavailable")
    return lane.services.journal


@router.post("/journal/trades/query", response_model=TradeHistoryPage,
    openapi_extra=request_schema(JournalQuery))
async def trade_history(request: Request, query: JournalQuery = Depends(body_contract(JournalQuery))):
    lane = get_lane(request)
    return await lane.call(lambda: wire_response(journal_owner(lane).trade_history(HistoryQuery(**query.model_dump(mode="python")))))


@router.post("/journal/research/query", response_model=ResearchHistoryPage,
    openapi_extra=request_schema(JournalQuery))
async def research_history(request: Request, query: JournalQuery = Depends(body_contract(JournalQuery))):
    lane = get_lane(request)
    return await lane.call(lambda: wire_response(journal_owner(lane).research_history(HistoryQuery(**query.model_dump(mode="python")))))


@router.get("/journal/sessions/{session_id}/summary", response_model=SessionSummary)
async def journal_summary(request: Request, session_id: Identity):
    lane = get_lane(request)
    return await lane.call(lambda: wire_response(journal_owner(lane).summary(session_id)))
