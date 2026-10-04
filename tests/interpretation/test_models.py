import asyncio
import json

from pydantic import ValidationError
import pytest

from quantlab.interpretation import (
    InterpretationContractError, InterpretationInput, InterpretationInputError,
    InterpretationResult, StructuredInterpretation, build_request, interpret_strategy,
)
from quantlab.llm import FakeProvider, LLMClient
from tests.llm.helpers import IDENTITY, INFO
from .helpers import clarification, raw, ready, run, source


@pytest.mark.parametrize("changes", [
    {"strategy_text": ""}, {"strategy_text": " \n\t"}, {"strategy_text": 1},
    {"strategy_text": True}, {"strategy_text": b"text"}, {"strategy_text": None},
    {"strategy_text": "a" * 16001}, {"input_reference": ""}, {"input_reference": "x y"},
    {"input_reference": "a" * 513}, {"input_reference": 1}, {"strategy_id": "a.b"},
    {"strategy_id": "a" * 129}, {"version": 0}, {"version": True}, {"version": "1"},
    {"api_key": "secret"}, {"created_at": "2026-01-01"}, {"approved": True},
])
def test_strict_bounded_input(changes):
    with pytest.raises(ValidationError):
        source(**changes)


def test_input_limits_roundtrip_and_frozen():
    value = source(strategy_text="a" * 16000, input_reference="a" * 512,
                   strategy_id="a" * 128)
    assert InterpretationInput.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        value.strategy_text = "changed"


@pytest.mark.parametrize("value", [None, {}, "text",
    source().model_copy(update={"strategy_text": " "}),
    InterpretationInput.model_construct(strategy_text=123, input_reference="x",
                                        strategy_id="x", version=1)])
def test_unchecked_input_fails_before_invocation(value):
    fake = FakeProvider(INFO, (raw(),))
    with pytest.raises(InterpretationInputError):
        asyncio.run(interpret_strategy(LLMClient(fake), value, identity=IDENTITY))
    assert fake.history == ()
    with pytest.raises(InterpretationInputError):
        build_request(value, identity=IDENTITY)


@pytest.mark.parametrize("changes", [
    {"draft": None}, {"evidence": []}, {"clarifications": clarification()["clarifications"]},
    {"status": "ready"}, {"status": 1}, {"confidence": 0.99}, {"assumptions": ["period 20"]},
])
def test_ready_contract_invariants(changes):
    with pytest.raises(ValidationError):
        StructuredInterpretation.model_validate_json(json.dumps(ready() | changes))


@pytest.mark.parametrize("changes", [
    {"draft": ready()["draft"]}, {"evidence": ready()["evidence"]}, {"clarifications": []},
    {"clarifications": [{"ambiguity": " ", "question": "Which?"}]},
    {"clarifications": [{"ambiguity": "Missing", "question": 1}]},
    {"clarifications": [{"ambiguity": "Missing", "question": "x" * 2001}]},
    {"clarifications": clarification()["clarifications"] * 33},
])
def test_clarification_contract_invariants(changes):
    with pytest.raises(ValidationError):
        StructuredInterpretation.model_validate_json(json.dumps(clarification() | changes))


def test_result_roundtrip_deep_frozen_and_draft_revalidation():
    result, _ = run()
    assert InterpretationResult.model_validate_json(result.model_dump_json()) == result
    draft = result.interpretation.draft
    for model, field, value in ((result, "proposal", None), (draft, "name", "changed"),
            (draft.features[0].parameters[0], "value", 5)):
        with pytest.raises(ValidationError):
            setattr(model, field, value)
    bad_feature = draft.features[0].model_copy(update={"parameters": (draft.features[0].parameters[0].model_copy(
        update={"value": {"code": "exec(x)"}}),)})
    forged = result.interpretation.model_copy(update={"draft": draft.model_copy(update={"features": (bad_feature,)})})
    with pytest.raises(ValidationError):
        StructuredInterpretation.model_validate(forged)


@pytest.mark.parametrize("change", ["proposal", "identity", "input", "prompt", "response", "interpretation"])
def test_result_revalidates_audit_binding(change):
    result, _ = run()
    if change == "proposal":
        forged = result.model_copy(update={"proposal": result.proposal.mark_validated()})
    elif change == "identity":
        forged = result.model_copy(update={"proposal": result.proposal.model_copy(update={"version": 2})})
    elif change == "input":
        forged = result.model_copy(update={"input": source(strategy_text="unrelated")})
    elif change == "prompt":
        req = result.invocation.request.model_copy(update={"messages": result.invocation.request.messages[1:]})
        forged = result.model_copy(update={"invocation": result.invocation.model_copy(update={"request": req})})
    elif change == "response":
        forged = result.model_copy(update={"invocation": result.invocation.model_copy(update={"response": raw(clarification())})})
    else:
        forged = result.model_copy(update={"interpretation": StructuredInterpretation.model_validate_json(json.dumps(clarification()))})
    with pytest.raises(InterpretationContractError):
        InterpretationResult.model_validate(forged)
