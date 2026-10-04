import asyncio
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from quantlab.interpretation import build_request, build_multimodal_request
from quantlab.llm import FakeProvider, InvocationPolicy, LLMClient, ProviderTimeoutError, RateLimitError, StructuredOutputError
from quantlab.orchestration import (
    InterpretationRoute, OrchestrationInputError, OrchestrationRequest, ReviewAction,
    WorkflowResumeError, WorkflowSnapshot, WorkflowStatus, build_research_graph,
    resume_workflow, start_workflow,
)
from quantlab.strategies import ApprovalState
from tests.interpretation import helpers as text, multimodal_helpers as vision
from tests.llm.helpers import IDENTITY
from .helpers import decision, setup


@pytest.mark.parametrize("multimodal", [False, True])
@pytest.mark.parametrize("action,status", [
    (ReviewAction.APPROVE, WorkflowStatus.ACCEPTED_FOR_APPROVAL),
    (ReviewAction.REJECT, WorkflowStatus.REJECTED),
    (ReviewAction.REQUEST_REVISION, WorkflowStatus.REVISION_REQUESTED),
])
def test_real_pause_resume_and_domain_boundary(multimodal, action, status):
    async def scenario():
        graph, request, fake = setup(multimodal=multimodal)
        first = await start_workflow(graph, request)
        assert first.status is WorkflowStatus.AWAITING_HUMAN_REVIEW
        assert first.route is (InterpretationRoute.MULTIMODAL if multimodal else InterpretationRoute.TEXT)
        assert first.request == request and first.decision is None
        checkpoint = await graph._compiled.aget_state(graph._config(request.thread_id))
        assert checkpoint.next == ("human_review",)
        payload = checkpoint.interrupts[0].value
        assert json.loads(json.dumps(payload)) == first.review.model_dump(mode="json")
        assert payload["strategy_id"] == request.input.strategy_id
        assert payload["version"] == request.input.version
        assert payload["content_digest"] == first.proposal.content_digest
        assert "images" not in payload and "invocation" not in payload
        expected = (build_multimodal_request if multimodal else build_request)(request.input, identity=IDENTITY)
        assert fake.history == (expected,)
        answer = decision(first, action)
        final = await resume_workflow(graph, thread_id=request.thread_id, decision=answer)
        assert final.status is status and final.decision == answer
        assert final.result == first.result and final.request == request
        assert final.proposal.state is ApprovalState.DRAFT and final.proposal.approval is None
        assert final.proposal.created_at is None and final.phase5_approval_occurred is False
        assert "Separate exact-version Phase 5" in final.approval_boundary
        assert fake.history == (expected,)
        assert WorkflowSnapshot.model_validate_json(final.model_dump_json()) == final
        assert not (await graph._compiled.aget_state(graph._config(request.thread_id))).next
        with pytest.raises(WorkflowResumeError):
            await resume_workflow(graph, thread_id=request.thread_id, decision=answer)
        return final
    final = asyncio.run(scenario())
    from tests.backtesting.helpers import simulate
    from quantlab.backtesting import BacktestCompatibilityError
    with pytest.raises(BacktestCompatibilityError):
        simulate(spec=final.proposal)


@pytest.mark.parametrize("multimodal", [False, True])
def test_clarification_is_terminal_with_no_review_or_retry(multimodal):
    async def scenario():
        helper = vision if multimodal else text
        output = helper.clarification()
        graph, request, fake = setup(multimodal=multimodal, output=output)
        final = await start_workflow(graph, request)
        assert final.status is WorkflowStatus.NEEDS_CLARIFICATION
        assert final.proposal is None and final.review is None and final.decision is None
        assert [c.model_dump() for c in final.clarifications] == output["clarifications"]
        checkpoint = await graph._compiled.aget_state(graph._config(request.thread_id))
        assert not checkpoint.next and not checkpoint.interrupts
        assert len(fake.history) == 1 and final.request == request
    asyncio.run(scenario())


def test_multimodal_conflicts_end_without_review_and_preserve_order():
    async def scenario():
        graph, request, fake = setup(multimodal=True, output=vision.conflict(images_only=True))
        final = await start_workflow(graph, request)
        assert final.status is WorkflowStatus.NEEDS_CLARIFICATION
        assert final.review is None and final.proposal is None
        assert final.result.interpretation.conflicts
        assert final.result.input.images == vision.IMAGES
        assert fake.history[0] == build_multimodal_request(request.input, identity=IDENTITY)
    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [True, False, "APPROVE", {}, {"action": "APPROVE"}, None, 1])
def test_untyped_decisions_cannot_resume(bad):
    async def scenario():
        graph, request, fake = setup()
        first = await start_workflow(graph, request)
        with pytest.raises(WorkflowResumeError):
            await resume_workflow(graph, thread_id=request.thread_id, decision=bad)
        final = await resume_workflow(graph, thread_id=request.thread_id, decision=decision(first))
        assert final.status is WorkflowStatus.ACCEPTED_FOR_APPROVAL and len(fake.history) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("thread_id", "other"), ("strategy_id", "other"),
    ("version", 2), ("content_digest", "0" * 64)])
def test_mismatched_decision_does_not_consume_interrupt(field, value):
    async def scenario():
        graph, request, fake = setup()
        first = await start_workflow(graph, request)
        with pytest.raises(WorkflowResumeError):
            await resume_workflow(graph, thread_id=request.thread_id, decision=decision(first, **{field: value}))
        final = await resume_workflow(graph, thread_id=request.thread_id, decision=decision(first))
        assert final.result == first.result and len(fake.history) == 1
    asyncio.run(scenario())


def test_threads_isolated_and_duplicate_starts_rejected():
    async def scenario():
        fake = FakeProvider(vision.INFO, (text.raw(), vision.raw()))
        graph = build_research_graph(client=LLMClient(fake), identity=IDENTITY, checkpointer=InMemorySaver())
        a = OrchestrationRequest(thread_id="A", input=text.source())
        b = OrchestrationRequest(thread_id="B", input=vision.source(version=2))
        first = await start_workflow(graph, a)
        second = await start_workflow(graph, b)
        for req in (a, OrchestrationRequest(thread_id="A", input=text.source(version=20))):
            with pytest.raises(OrchestrationInputError):
                await start_workflow(graph, req)
        for thread, answer in (("B", decision(first)), ("absent", decision(first, thread_id="absent")),
                               ("B", decision(first, thread_id="B"))):
            with pytest.raises(WorkflowResumeError):
                await resume_workflow(graph, thread_id=thread, decision=answer)
        resumed = await resume_workflow(graph, thread_id="A", decision=decision(first))
        rejected = await resume_workflow(graph, thread_id="B", decision=decision(second, ReviewAction.REJECT))
        assert resumed.result == first.result and rejected.result == second.result
        assert resumed.decision != rejected.decision and len(fake.history) == 2
    asyncio.run(scenario())


def test_determinism_and_fresh_default_checkpointers():
    async def scenario():
        request = OrchestrationRequest(thread_id="A", input=text.source())
        snapshots = []
        for _ in range(2):
            fake = FakeProvider(vision.INFO, (text.raw(),))
            graph = build_research_graph(client=LLMClient(fake), identity=IDENTITY)
            snapshots.append(await start_workflow(graph, request))
        assert snapshots[0] == snapshots[1]
    asyncio.run(scenario())


@pytest.mark.parametrize("multimodal", [False, True])
@pytest.mark.parametrize("failure", [ProviderTimeoutError, RateLimitError])
def test_phase13_retry_and_error_types_unchanged(multimodal, failure):
    async def scenario():
        helper = vision if multimodal else text
        request = OrchestrationRequest(thread_id="retry", input=helper.source())
        for succeed in (True, False):
            fake = FakeProvider(vision.INFO, (failure(), helper.raw() if succeed else failure()))
            client = LLMClient(fake, policy=InvocationPolicy(max_attempts=2))
            graph = build_research_graph(client=client, identity=IDENTITY)
            if succeed:
                first = await start_workflow(graph, request)
                assert first.result.invocation.attempts == 2
            else:
                with pytest.raises(failure):
                    await start_workflow(graph, request)
                with pytest.raises(OrchestrationInputError):
                    await start_workflow(graph, request)
            assert len(fake.history) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("multimodal", [False, True])
def test_invalid_output_retains_phase13_error_without_graph_retry(multimodal):
    async def scenario():
        helper = vision if multimodal else text
        fake = FakeProvider(vision.INFO, (helper.raw(text="private malformed output"),))
        graph = build_research_graph(client=LLMClient(fake), identity=IDENTITY)
        with pytest.raises(StructuredOutputError) as caught:
            await start_workflow(graph, OrchestrationRequest(thread_id="bad", input=helper.source()))
        assert "private" not in str(caught.value) and len(fake.history) == 1
    asyncio.run(scenario())


def test_concurrent_duplicate_start_and_resume_are_serialized():
    async def scenario():
        graph, request, fake = setup()
        results = await asyncio.gather(start_workflow(graph, request), start_workflow(graph, request),
                                       return_exceptions=True)
        assert sum(isinstance(r, OrchestrationInputError) for r in results) == 1
        first = next(r for r in results if isinstance(r, WorkflowSnapshot))
        results = await asyncio.gather(*[resume_workflow(graph, thread_id=request.thread_id,
            decision=decision(first)) for _ in range(2)], return_exceptions=True)
        assert sum(isinstance(r, WorkflowResumeError) for r in results) == 1
        assert len(fake.history) == 1
    asyncio.run(scenario())
