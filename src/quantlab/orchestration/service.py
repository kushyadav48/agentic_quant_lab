"""Small application API; no caller-supplied LangGraph configuration or commands."""
import asyncio
from contextvars import Context
import os

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from quantlab.llm import LLMClient, ModelIdentity

from .enums import WorkflowStatus
from .errors import OrchestrationContractError, OrchestrationInputError, WorkflowResumeError
from .graph import create_graph, read_state, write_state
from .models import (
    HumanReviewDecision, OrchestrationRequest, WorkflowReference, WorkflowSnapshot, decision_matches,
)


class ResearchGraph:
    """Own one in-memory workflow namespace. Treat this handle/checkpointer as trusted.

    Calls on a handle are serialized, including duplicate-start and resume checks.
    Use the same handle from one event loop; do not share its saver with other handles.
    """

    def __init__(self, compiled) -> None:
        self._compiled = compiled
        self._lock = asyncio.Lock()

    @staticmethod
    def _config(thread_id: str):
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 8,
                "callbacks": []}

    async def _invoke(self, value, thread_id: str) -> None:
        # LangGraph transitively configures tracing from these non-secret flags.
        # Fail closed rather than import a tracing SDK or mutate process settings.
        # Never inspect API keys or other environment credentials.
        for flag in ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2",
                     "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_HANDLER"):
            if os.getenv(flag, "").lower() not in ("", "false", "0"):
                raise OrchestrationInputError()
        # A clean context also drops inherited callbacks, tracing contexts and
        # runnable configuration from a surrounding application. The task is awaited.
        await asyncio.create_task(self._compiled.ainvoke(value, config=self._config(thread_id)),
                                  context=Context())

    async def _snapshot(self, thread_id: str) -> WorkflowSnapshot:
        checkpoint = await self._compiled.aget_state(self._config(thread_id))
        if not checkpoint.values:
            raise WorkflowResumeError()
        snapshot = read_state(checkpoint.values)
        if snapshot.request.thread_id != thread_id:
            raise OrchestrationContractError()
        if snapshot.status is WorkflowStatus.AWAITING_HUMAN_REVIEW:
            if (checkpoint.next != ("human_review",) or len(checkpoint.interrupts) != 1
                    or checkpoint.interrupts[0].value != snapshot.review.model_dump(mode="json")):
                raise OrchestrationContractError()
        elif snapshot.status is WorkflowStatus.INTERPRETING or checkpoint.next or checkpoint.interrupts:
            raise OrchestrationContractError()
        return snapshot


def build_research_graph(*, client: LLMClient, identity: ModelIdentity,
                         checkpointer: InMemorySaver | None = None) -> ResearchGraph:
    try:
        if not isinstance(client, LLMClient) or type(identity) is not ModelIdentity:
            raise OrchestrationInputError()
        identity = ModelIdentity.model_validate(identity)
        if checkpointer is not None and type(checkpointer) is not InMemorySaver:
            raise OrchestrationInputError()
    except ValidationError:
        raise OrchestrationInputError() from None
    saver = InMemorySaver() if checkpointer is None else checkpointer
    return ResearchGraph(create_graph(client=client, identity=identity, checkpointer=saver))


async def start_workflow(graph: ResearchGraph, request: OrchestrationRequest) -> WorkflowSnapshot:
    try:
        if type(graph) is not ResearchGraph or type(request) is not OrchestrationRequest:
            raise OrchestrationInputError()
        request = OrchestrationRequest.model_validate(request)
    except (ValueError, TypeError):
        raise OrchestrationInputError() from None
    async with graph._lock:
        config = graph._config(request.thread_id)
        if (await graph._compiled.aget_state(config)).values:
            raise OrchestrationInputError()
        initial = WorkflowSnapshot(request=request, status=WorkflowStatus.INTERPRETING)
        await graph._invoke(write_state(initial), request.thread_id)
        return await graph._snapshot(request.thread_id)


async def resume_workflow(graph: ResearchGraph, *, thread_id: str,
                          decision: HumanReviewDecision) -> WorkflowSnapshot:
    try:
        if type(graph) is not ResearchGraph or type(decision) is not HumanReviewDecision:
            raise WorkflowResumeError()
        decision = HumanReviewDecision.model_validate(decision)
        if type(thread_id) is not str or thread_id != decision.thread_id:
            raise WorkflowResumeError()
    except (ValueError, TypeError):
        raise WorkflowResumeError() from None
    async with graph._lock:
        snapshot = await graph._snapshot(thread_id)
        if (snapshot.status is not WorkflowStatus.AWAITING_HUMAN_REVIEW
                or not decision_matches(snapshot.review, decision)):
            raise WorkflowResumeError()
        await graph._invoke(Command(resume=decision.model_dump_json()), thread_id)
        return await graph._snapshot(thread_id)


async def get_workflow_snapshot(graph: ResearchGraph, *, thread_id: str) -> WorkflowSnapshot:
    """Read validated retained state; never invoke, resume or approve a workflow."""
    if type(graph) is not ResearchGraph:
        raise OrchestrationInputError()
    try:
        reference = WorkflowReference(thread_id=thread_id)
    except ValidationError:
        raise OrchestrationInputError() from None
    async with graph._lock:
        return await graph._snapshot(reference.thread_id)
