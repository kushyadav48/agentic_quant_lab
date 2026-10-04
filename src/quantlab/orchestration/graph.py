"""Explicit acyclic workflow; only Phase 14/15 services invoke the LLM client."""
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import StringConstraints, ValidationError

from quantlab import interpretation
from quantlab.interpretation import InterpretationError, InterpretationStatus
from quantlab.llm import LLMClient, LLMError, ModelIdentity

from .enums import InterpretationRoute, WorkflowStatus
from .errors import OrchestrationContractError
from .models import (
    HumanReviewDecision, WorkflowSnapshot, decision_matches, decision_status, review_for,
)


MAX_SNAPSHOT_CHARS = 32_000_000


class WorkflowState(TypedDict):
    # One serialized strict snapshot, revalidated on every read. JSON transport
    # avoids arbitrary Python-object deserialization and duplicated proposal state.
    snapshot_json: Annotated[str, StringConstraints(min_length=2, max_length=MAX_SNAPSHOT_CHARS)]


def read_state(state: WorkflowState) -> WorkflowSnapshot:
    try:
        if set(state) != {"snapshot_json"} or type(state["snapshot_json"]) is not str:
            raise OrchestrationContractError()
        if not 2 <= len(state["snapshot_json"]) <= MAX_SNAPSHOT_CHARS:
            raise OrchestrationContractError()
        return WorkflowSnapshot.model_validate_json(state["snapshot_json"])
    except (ValueError, TypeError, KeyError, InterpretationError, LLMError):
        raise OrchestrationContractError() from None


def write_state(snapshot: WorkflowSnapshot) -> WorkflowState:
    try:
        encoded = WorkflowSnapshot.model_validate(snapshot).model_dump_json()
        if len(encoded) > MAX_SNAPSHOT_CHARS:
            raise OrchestrationContractError()
        return {"snapshot_json": encoded}
    except (ValueError, TypeError, InterpretationError, LLMError):
        raise OrchestrationContractError() from None


def create_graph(*, client: LLMClient, identity: ModelIdentity, checkpointer):
    async def route_input(state: WorkflowState):
        snapshot = read_state(state)
        if snapshot.status is not WorkflowStatus.INTERPRETING:
            raise OrchestrationContractError()
        return write_state(snapshot)

    async def input_destination(state: WorkflowState):
        return read_state(state).route

    def interpreted(snapshot, result):
        try:
            status = (WorkflowStatus.NEEDS_CLARIFICATION
                      if result.status is InterpretationStatus.NEEDS_CLARIFICATION
                      else WorkflowStatus.AWAITING_HUMAN_REVIEW)
            return write_state(WorkflowSnapshot(request=snapshot.request, result=result,
                status=status, review=None if status is WorkflowStatus.NEEDS_CLARIFICATION
                else review_for(snapshot.request, result)))
        except (ValueError, TypeError, AttributeError, InterpretationError, LLMError):
            raise OrchestrationContractError() from None

    async def interpret_text(state: WorkflowState):
        snapshot = read_state(state)
        result = await interpretation.interpret_strategy(client, snapshot.request.input, identity=identity)
        return interpreted(snapshot, result)

    async def interpret_multimodal(state: WorkflowState):
        snapshot = read_state(state)
        result = await interpretation.interpret_multimodal_strategy(client, snapshot.request.input,
                                                                   identity=identity)
        return interpreted(snapshot, result)

    async def interpretation_destination(state: WorkflowState):
        return read_state(state).status

    async def human_review(state: WorkflowState):
        snapshot = read_state(state)
        if snapshot.status is not WorkflowStatus.AWAITING_HUMAN_REVIEW:
            raise OrchestrationContractError()
        # This node restarts after resume; no interpretation or side effects precede interrupt.
        resumed = interrupt(snapshot.review.model_dump(mode="json"))
        try:
            if type(resumed) is not str:
                raise OrchestrationContractError()
            decision = HumanReviewDecision.model_validate_json(resumed)
        except (ValidationError, TypeError):
            raise OrchestrationContractError() from None
        if not decision_matches(snapshot.review, decision):
            raise OrchestrationContractError()
        return write_state(WorkflowSnapshot(request=snapshot.request, result=snapshot.result,
            review=snapshot.review, decision=decision, status=decision_status(decision.action)))

    graph = StateGraph(WorkflowState)
    graph.add_node("route_input", route_input)
    graph.add_node("interpret_text", interpret_text)
    graph.add_node("interpret_multimodal", interpret_multimodal)
    graph.add_node("human_review", human_review)
    graph.add_edge(START, "route_input")
    graph.add_conditional_edges("route_input", input_destination, {
        InterpretationRoute.TEXT: "interpret_text",
        InterpretationRoute.MULTIMODAL: "interpret_multimodal"})
    for node in ("interpret_text", "interpret_multimodal"):
        graph.add_conditional_edges(node, interpretation_destination, {
            WorkflowStatus.NEEDS_CLARIFICATION: END,
            WorkflowStatus.AWAITING_HUMAN_REVIEW: "human_review"})
    graph.add_edge("human_review", END)
    return graph.compile(checkpointer=checkpointer)
