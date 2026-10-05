"""Read-only retained-state API added for the Phase 17 application handoff."""
import asyncio

import pytest

from quantlab.orchestration import (
    OrchestrationInputError, WorkflowResumeError, get_workflow_snapshot, start_workflow,
)
from .helpers import setup


@pytest.mark.parametrize("thread_id", [None, 1, "", "bad/thread", "thread\n"])
def test_snapshot_rejects_invalid_reference(thread_id):
    async def scenario():
        graph, _, provider = setup()
        with pytest.raises(OrchestrationInputError):
            await get_workflow_snapshot(graph, thread_id=thread_id)
        assert not provider.history
    asyncio.run(scenario())


def test_snapshot_missing_thread_and_invalid_handle():
    async def scenario():
        graph, request, provider = setup()
        with pytest.raises(WorkflowResumeError):
            await get_workflow_snapshot(graph, thread_id=request.thread_id)
        with pytest.raises(OrchestrationInputError):
            await get_workflow_snapshot(object(), thread_id=request.thread_id)
        assert not provider.history
    asyncio.run(scenario())


def test_snapshot_is_immutable_read_without_review_resume():
    async def scenario():
        graph, request, provider = setup()
        paused = await start_workflow(graph, request)
        assert await get_workflow_snapshot(graph, thread_id=request.thread_id) == paused
        assert await get_workflow_snapshot(graph, thread_id=request.thread_id) == paused
        assert len(provider.history) == 1
    asyncio.run(scenario())
