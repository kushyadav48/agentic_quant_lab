import asyncio

import pytest
from pydantic import ValidationError

from quantlab.orchestration import (
    HumanReviewDecision, OrchestrationContractError, OrchestrationInputError,
    OrchestrationRequest, ReviewAction, WorkflowResumeError, WorkflowSnapshot,
    WorkflowStatus, resume_workflow, start_workflow,
)
from quantlab.orchestration.graph import read_state, write_state
from tests.interpretation import helpers as text, multimodal_helpers as vision
from .helpers import decision, setup


@pytest.mark.parametrize("source", [text.source, vision.source])
def test_valid_request_roundtrip_frozen_and_extra_forbid(source):
    request = OrchestrationRequest(thread_id="app_owned_16", input=source())
    assert OrchestrationRequest.model_validate_json(request.model_dump_json()) == request
    for obj, field, value in ((request, "thread_id", "other"), (request.input, "version", 2)):
        with pytest.raises(ValidationError):
            setattr(obj, field, value)
    with pytest.raises(ValidationError):
        OrchestrationRequest(thread_id="A", input=source(), credentials="private")


@pytest.mark.parametrize("bad", [None, True, 1, "", " ", "a" * 129, "a/b", "a\nb"])
def test_thread_identity_strict_required_and_bounded(bad):
    with pytest.raises(ValidationError):
        OrchestrationRequest(thread_id=bad, input=text.source())


def test_no_generated_thread_default():
    with pytest.raises(ValidationError):
        OrchestrationRequest(input=text.source())


@pytest.mark.parametrize("bad", [None, True, "idea", {}, (text.source(), vision.source()), text.ready()])
def test_wrong_union_member_rejected(bad):
    with pytest.raises(ValidationError):
        OrchestrationRequest(thread_id="A", input=bad)


@pytest.mark.parametrize("multimodal", [False, True])
def test_unchecked_nested_inputs_revalidated_before_provider(multimodal):
    async def scenario():
        graph, request, fake = setup(multimodal=multimodal)
        bad = request.input.model_copy(update={"version": True})
        if multimodal:
            image = request.input.images[0].model_copy(update={"asset_id": "https://private/asset"})
            bad = request.input.model_copy(update={"images": (image,)})
        forged = request.model_copy(update={"input": bad})
        with pytest.raises(OrchestrationInputError):
            await start_workflow(graph, forged)
        assert not fake.history
    asyncio.run(scenario())


@pytest.mark.parametrize("action", [True, False, 1, "APPROVE", {}, None])
def test_action_requires_enum_on_python_boundary(action):
    with pytest.raises(ValidationError):
        HumanReviewDecision(thread_id="A", strategy_id="strategy", version=1,
                            content_digest="a" * 64, action=action)


def test_decision_extra_fields_and_deep_revalidation():
    async def scenario():
        graph, request, fake = setup()
        first = await start_workflow(graph, request)
        answer = decision(first)
        with pytest.raises(ValidationError):
            HumanReviewDecision(**answer.model_dump(), reviewer="invented")
        with pytest.raises(ValidationError):
            answer.action = ReviewAction.REJECT
        with pytest.raises(WorkflowResumeError):
            await resume_workflow(graph, thread_id=request.thread_id,
                decision=answer.model_copy(update={"action": True}))
        assert len(fake.history) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("multimodal", [False, True])
@pytest.mark.parametrize("change", ["input", "input_version", "result_input", "proposal_version",
    "proposal_id", "proposal_content", "interpretation", "response", "review_digest",
    "review_source", "review_route", "status", "decision", "approval"])
def test_forged_snapshot_fails_closed(multimodal, change):
    async def scenario():
        graph, request, _ = setup(multimodal=multimodal)
        first = await start_workflow(graph, request)
        result = first.result
        if change == "input":
            altered = request.input.model_copy(update={"strategy_text": "private changed input"})
            forged = first.model_copy(update={"request": request.model_copy(update={"input": altered})})
        elif change == "input_version":
            altered = request.input.model_copy(update={"version": 3})
            forged = first.model_copy(update={"request": request.model_copy(update={"input": altered})})
        elif change == "result_input":
            altered = result.input.model_copy(update={"strategy_text": "private changed input"})
            forged = first.model_copy(update={"result": result.model_copy(update={"input": altered})})
        elif change.startswith("proposal_"):
            changes = {"proposal_version": {"version": 2}, "proposal_id": {"strategy_id": "other"},
                "proposal_content": {"content": result.proposal.content.model_copy(update={"name": "other"})}}
            proposal = result.proposal.model_copy(update=changes[change])
            forged = first.model_copy(update={"result": result.model_copy(update={"proposal": proposal})})
        elif change == "interpretation":
            draft = result.interpretation.draft.model_copy(update={"name": "other"})
            altered = result.interpretation.model_copy(update={"draft": draft})
            forged = first.model_copy(update={"result": result.model_copy(update={"interpretation": altered})})
        elif change == "response":
            altered = result.invocation.model_copy(update={"response": text.raw(text="private invalid")})
            forged = first.model_copy(update={"result": result.model_copy(update={"invocation": altered})})
        elif change.startswith("review_"):
            changes = {"review_digest": {"content_digest": "0" * 64},
                "review_source": {"source_reference": "other"}, "review_route": {"route": "OTHER"}}
            forged = first.model_copy(update={"review": first.review.model_copy(update=changes[change])})
        elif change == "status":
            forged = first.model_copy(update={"status": WorkflowStatus.ACCEPTED_FOR_APPROVAL})
        elif change == "decision":
            forged = first.model_copy(update={"status": WorkflowStatus.ACCEPTED_FOR_APPROVAL,
                                              "decision": decision(first, version=4)})
        else:
            forged = first.model_copy(update={"phase5_approval_occurred": True})
        with pytest.raises(OrchestrationContractError) as caught:
            write_state(forged)
        assert "private" not in str(caught.value)
    asyncio.run(scenario())


def test_reordered_images_fail_replay_binding():
    async def scenario():
        graph, request, _ = setup(multimodal=True)
        first = await start_workflow(graph, request)
        altered = request.input.model_copy(update={"images": tuple(reversed(request.input.images))})
        # Alter both copies of the input: the Phase 15 invocation still binds image order.
        forged = first.model_copy(update={
            "request": request.model_copy(update={"input": altered}),
            "result": first.result.model_copy(update={"input": altered})})
        with pytest.raises(OrchestrationContractError):
            write_state(forged)
    asyncio.run(scenario())


@pytest.mark.parametrize("state", [{}, {"snapshot_json": True}, {"snapshot_json": "private"},
    {"snapshot_json": "{}"}, {"snapshot_json": "{}", "client": "private"}])
def test_malformed_checkpoint_transport_is_sanitized(state):
    with pytest.raises(OrchestrationContractError) as caught:
        read_state(state)
    assert "private" not in str(caught.value)


def test_checkpoint_review_mutation_cannot_resume():
    async def scenario():
        graph, request, fake = setup()
        first = await start_workflow(graph, request)
        forged = first.model_copy(update={"review": first.review.model_copy(update={"version": 9})})
        # Public LangGraph update API simulates corrupted trusted storage; no private saver internals.
        with pytest.raises(OrchestrationContractError):
            await graph._compiled.aupdate_state(graph._config(request.thread_id),
                {"snapshot_json": forged.model_dump_json()}, as_node="interpret_text")
        final = await resume_workflow(graph, thread_id=request.thread_id, decision=decision(first))
        assert final.result == first.result
        assert len(fake.history) == 1
    asyncio.run(scenario())


def test_unchecked_interpretation_service_result_is_sanitized(monkeypatch):
    import quantlab.interpretation as interpretation
    result, _ = text.run()
    forged = result.model_copy(update={"input": text.source(strategy_text="private unrelated")})
    async def replaced(*args, **kwargs):
        return forged
    monkeypatch.setattr(interpretation, "interpret_strategy", replaced)
    async def scenario():
        graph, request, fake = setup()
        with pytest.raises(OrchestrationContractError) as caught:
            await start_workflow(graph, request)
        assert "private" not in str(caught.value) and not fake.history
    asyncio.run(scenario())
