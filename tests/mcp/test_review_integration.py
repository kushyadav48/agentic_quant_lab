"""Retained Phase 16 review is distinct from separate Phase 5 approval."""
import ast
import asyncio
from pathlib import Path

import pytest

from quantlab import analytics, backtesting
from quantlab.mcp.operation_models import OperationState
from quantlab.mcp.operations import OperationInputError, ResearchOperations
from quantlab.mcp.review_integration import ReviewedResearchRequest, run_reviewed_backtest
from quantlab.orchestration import (
    ReviewAction, WorkflowStatus, get_workflow_snapshot, resume_workflow, start_workflow,
)
from quantlab.strategies import ApprovalState, StrategySpecification
from tests.backtesting.helpers import CONFIG, INSTRUMENT, approve, bars, observations
from tests.interpretation.helpers import clarification
from tests.orchestration.helpers import decision, setup


def research_request(snapshot, *, approved=True, **changes):
    spec = approve(snapshot.proposal) if approved else snapshot.proposal
    series = bars((101, 110, 99, 90, 105, 111))
    return ReviewedResearchRequest(**(dict(thread_id=snapshot.request.thread_id,
        idempotency_key="review_run", strategy=spec, bars=series,
        features=observations(spec, series), instrument=INSTRUMENT, config=CONFIG) | changes))


@pytest.mark.parametrize("multimodal", [False, True])
def test_actual_review_separate_approval_fixed_two_calls_and_idempotency(multimodal, monkeypatch):
    async def scenario():
        graph, request, provider = setup(multimodal=multimodal)
        paused = await start_workflow(graph, request)
        accepted = await resume_workflow(graph, thread_id=request.thread_id, decision=decision(paused))
        assert accepted.status is WorkflowStatus.ACCEPTED_FOR_APPROVAL
        assert accepted.proposal.state is ApprovalState.DRAFT and accepted.proposal.approval is None
        research = research_request(accepted)
        namespace = ResearchOperations()
        original_run, original_analysis = backtesting.run_backtest, analytics.analyze_performance
        calls = []
        def run(*args, **kwargs):
            calls.append("backtest")
            return original_run(*args, **kwargs)
        def analyze(*args, **kwargs):
            calls.append("performance")
            return original_analysis(*args, **kwargs)
        def denied(*args, **kwargs):
            raise AssertionError("human approval or interpretation authority forbidden")
        with monkeypatch.context() as patch:
            patch.setattr(backtesting, "run_backtest", run)
            patch.setattr(analytics, "analyze_performance", analyze)
            # Retained snapshots may reconstruct existing canonical models;
            # block approval/lifecycle actions rather than deserialization.
            for name in ("approve", "mark_validated", "revise"):
                patch.setattr(StrategySpecification, name, denied)
            result = await run_reviewed_backtest(graph, namespace, research)
            assert await run_reviewed_backtest(graph, namespace, research) == result
        assert calls == ["backtest", "performance"] and len(provider.history) == 1
        assert result.backtest.state is result.performance.state is OperationState.COMPLETED
        assert result.content_digest == accepted.review.content_digest
        assert result.version == accepted.review.version and result.strategy_id == accepted.review.strategy_id
        for snapshot in (result.backtest, result.performance):
            assert snapshot.workflow.thread_digest == result.thread_digest
            assert snapshot.workflow.content_digest == accepted.review.content_digest
            assert all(event.workflow == snapshot.workflow for event in snapshot.audit)
        assert await get_workflow_snapshot(graph, thread_id=request.thread_id) == accepted
    asyncio.run(scenario())


def test_handoff_has_no_direct_approval_or_specification_construction():
    path = Path(__file__).parents[2] / "src" / "quantlab" / "mcp" / "review_integration.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            assert name not in {"ApprovalRecord", "StrategySpecification", "approve",
                                "mark_validated", "revise"}


@pytest.mark.parametrize("action", [None, ReviewAction.REJECT, ReviewAction.REQUEST_REVISION])
def test_unaccepted_review_cannot_execute_and_preserves_original_path(action):
    async def scenario():
        graph, request, provider = setup()
        paused = await start_workflow(graph, request)
        snapshot = paused if action is None else await resume_workflow(
            graph, thread_id=request.thread_id, decision=decision(paused, action))
        namespace = ResearchOperations()
        with pytest.raises(OperationInputError):
            await run_reviewed_backtest(graph, namespace, research_request(snapshot))
        assert not namespace._snapshots and len(provider.history) == 1
        assert await get_workflow_snapshot(graph, thread_id=request.thread_id) == snapshot
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["draft", "id", "version", "digest", "other_thread"])
def test_exact_thread_id_version_digest_binding(case):
    async def scenario():
        graph, request, provider = setup()
        paused = await start_workflow(graph, request)
        accepted = await resume_workflow(graph, thread_id=request.thread_id, decision=decision(paused))
        spec = accepted.proposal
        changes = {}
        if case == "id":
            spec = StrategySpecification(strategy_id="different", version=spec.version, content=spec.content)
        elif case == "version":
            spec = spec.revise(spec.content)
        elif case == "digest":
            content = spec.content.model_dump(mode="python")
            content["name"] = "Changed content"
            spec = spec.revise(type(spec.content)(**content))
        elif case == "other_thread":
            # A missing second thread cannot borrow retained review from the first.
            changes["thread_id"] = "other_thread"
        research = research_request(accepted, approved=case != "draft")
        if case in {"id", "version", "digest"}:
            research = ReviewedResearchRequest(**(research.model_dump(mode="python") | {"strategy": approve(spec)}))
        namespace = ResearchOperations()
        if case == "other_thread":
            from quantlab.orchestration import WorkflowResumeError
            research = ReviewedResearchRequest(**(research.model_dump(mode="python") | changes))
            with pytest.raises(WorkflowResumeError):
                await run_reviewed_backtest(graph, namespace, research)
        else:
            with pytest.raises(OperationInputError):
                await run_reviewed_backtest(graph, namespace, research)
        assert not namespace._snapshots
    asyncio.run(scenario())


def test_clarification_snapshot_is_readable_without_invocation():
    async def scenario():
        graph, request, provider = setup(output=clarification())
        snapshot = await start_workflow(graph, request)
        assert snapshot.status is WorkflowStatus.NEEDS_CLARIFICATION
        assert await get_workflow_snapshot(graph, thread_id=request.thread_id) == snapshot
        assert len(provider.history) == 1
    asyncio.run(scenario())


def test_failed_backtest_stops_before_performance_and_graph_stays_accepted(monkeypatch):
    async def scenario():
        graph, request, provider = setup()
        paused = await start_workflow(graph, request)
        accepted = await resume_workflow(graph, thread_id=request.thread_id, decision=decision(paused))
        research = research_request(accepted, instrument=INSTRUMENT.model_copy(
            update={"instrument_id": "different_instrument"}))
        def denied(*args, **kwargs):
            raise AssertionError("performance called after failed backtest")
        monkeypatch.setattr(analytics, "analyze_performance", denied)
        namespace = ResearchOperations()
        result = await run_reviewed_backtest(graph, namespace, research)
        assert result.backtest.state is OperationState.FAILED and result.performance is None
        assert len(namespace._snapshots) == 1 and len(provider.history) == 1
        assert await get_workflow_snapshot(graph, thread_id=request.thread_id) == accepted
    asyncio.run(scenario())
