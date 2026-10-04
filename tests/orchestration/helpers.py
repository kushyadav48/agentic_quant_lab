from langgraph.checkpoint.memory import InMemorySaver

from quantlab.llm import FakeProvider, LLMClient
from quantlab.orchestration import (
    HumanReviewDecision, OrchestrationRequest, ReviewAction, build_research_graph,
)
from tests.interpretation import helpers as text, multimodal_helpers as vision
from tests.llm.helpers import IDENTITY


def setup(*, multimodal=False, output=None, thread_id="workflow_A", source=None, policy=None):
    helper = vision if multimodal else text
    fake = FakeProvider(vision.INFO, (helper.raw(output),))
    graph = build_research_graph(client=LLMClient(fake, **({} if policy is None else {"policy": policy})),
        identity=IDENTITY, checkpointer=InMemorySaver())
    request = OrchestrationRequest(thread_id=thread_id,
        input=helper.source() if source is None else source)
    return graph, request, fake


def decision(snapshot, action=ReviewAction.APPROVE, **changes):
    review = snapshot.review
    return HumanReviewDecision(**(dict(thread_id=review.thread_id,
        strategy_id=review.strategy_id, version=review.version,
        content_digest=review.content_digest, action=action) | changes))
