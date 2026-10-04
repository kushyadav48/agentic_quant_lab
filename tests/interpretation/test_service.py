import asyncio
import json

from pydantic import ValidationError
import pytest

import quantlab.llm as llm
from quantlab.interpretation import (
    InterpretedStrategyError, InterpretationContractError, InterpretationStatus,
    PROMPT_ID, PROMPT_VERSION, StructuredInterpretation, build_request, interpret_strategy,
)
from quantlab.strategies import ApprovalState, Origin, StrategySpecification, validate_content
from tests.llm.helpers import IDENTITY, INFO
from .helpers import TEXT, clarification, raw, ready, run, source


def test_ready_preserves_exact_domain_values_and_provenance():
    result, fake = run()
    spec = result.proposal
    assert type(spec) is StrategySpecification
    assert spec.state is ApprovalState.DRAFT and spec.approval is None and spec.created_at is None
    assert (spec.strategy_id, spec.version) == ("interpreted", 1)
    assert spec.content.features == result.interpretation.draft.features
    assert spec.content.parameters == result.interpretation.draft.parameters
    assert spec.content.long == result.interpretation.draft.long
    assert spec.content.features[0].parameters[0].value == 2
    assert spec.content.parameters[0].default == 100
    assert spec.content.provenance.origin is Origin.NATURAL_LANGUAGE
    assert spec.content.provenance.source_reference == source().input_reference
    assert spec.content.provenance.author_reference is None
    validate_content(spec.content)
    assert spec.content_digest == spec.content.content_digest()
    assert StrategySpecification.model_validate(spec) == spec
    assert result.invocation.response == raw()
    assert result.invocation.request == fake.history[0]
    assert result.invocation.response.identity == IDENTITY
    assert result.invocation.request.provenance.prompt_id == PROMPT_ID
    assert result.invocation.request.provenance.prompt_version == PROMPT_VERSION


@pytest.mark.parametrize("text,ambiguity,question", [
    ("Buy above SMA", "SMA period is missing", "Which period?"),
    ("Buy when RSI is low", "RSI threshold is unspecified", "Which RSI threshold?"),
    ("Buy near the average", "Entry comparison is ambiguous", "What defines near?"),
    ("Go long when close exceeds 100", "Exit intent is absent", "What exit or explicit holding policy?"),
    ("Use ATR(14)", "ATR is not implemented", "Would you supply a supported rule?"),
    ("Trade when price rises", "Direction and timeframe are absent", "Which side and timeframe?"),
    ("Use an ML forecast", "Model identity is missing", "Which existing model digest?"),
])
def test_clarification_retains_questions_without_proposal(text, ambiguity, question):
    result, _ = run(clarification(ambiguity, question), input=source(strategy_text=text))
    assert result.status is InterpretationStatus.NEEDS_CLARIFICATION
    assert result.proposal is None and result.interpretation.draft is None
    assert result.interpretation.clarifications[0].question == question
    assert result.interpretation.clarifications[0].ambiguity == ambiguity


@pytest.mark.parametrize("text", ["", "{", "not JSON", "[]", "null", "1", "{}",
    '```json\n{}\n```', '{"status":"READY","status":"NEEDS_CLARIFICATION"}',
    '{"status":NaN}', '{"status":Infinity}', json.dumps(ready()) + " trailing",
    json.dumps(ready()).replace('"period", "value": 2', '"period", "value": 2, "value": 3'),
])
def test_malformed_output_not_repaired_or_retried(text):
    fake = llm.FakeProvider(INFO, (raw(text=text), raw()))
    client = llm.LLMClient(fake, policy=llm.InvocationPolicy(max_attempts=3))
    with pytest.raises(llm.StructuredOutputError) as caught:
        asyncio.run(interpret_strategy(client, source(), identity=IDENTITY))
    assert len(fake.history) == 1
    assert str(caught.value) == llm.StructuredOutputError.message


@pytest.mark.parametrize("finish", [llm.FinishReason.LENGTH, llm.FinishReason.REFUSAL,
    llm.FinishReason.CONTENT_FILTER, llm.FinishReason.OTHER])
def test_unsupported_finish_reason(finish):
    fake = llm.FakeProvider(INFO, (raw(finish_reason=finish),))
    with pytest.raises(llm.StructuredOutputError):
        asyncio.run(interpret_strategy(llm.LLMClient(fake), source(), identity=IDENTITY))


@pytest.mark.parametrize("field,value", [("approval", {}), ("approved", True),
    ("state", "approved"), ("strategy_id", "model_chosen"), ("version", 999),
    ("provenance", {"author_reference": "approver:llm"}), ("created_at", "2026-01-01"),
    ("features", "sma(2)"), ("direction", 1), ("name", "x" * 201)])
def test_llm_cannot_supply_authority_or_wrong_types(field, value):
    wire = ready()
    wire["draft"][field] = value
    with pytest.raises(llm.StructuredOutputError):
        run(wire)


@pytest.mark.parametrize("field", ["instruments", "timeframe", "direction", "long", "short",
    "features", "parameters", "stop_loss", "take_profit", "session", "sizing_reference"])
def test_material_fields_cannot_be_omitted(field):
    wire = ready()
    del wire["draft"][field]
    with pytest.raises(llm.StructuredOutputError):
        run(wire)


@pytest.mark.parametrize("case", ["direction", "missing_feature", "missing_parameter",
    "boolean_comparison", "duplicate_feature", "risk_reward_without_stop"])
def test_shape_valid_output_still_fails_phase5_domain_validation(case):
    wire = ready()
    draft = wire["draft"]
    if case == "direction":
        draft["direction"] = "both"
    elif case == "missing_feature":
        draft["long"]["entry"]["rules"][0]["right"]["feature_id"] = "missing"
    elif case == "missing_parameter":
        draft["long"]["entry"]["rules"][1]["right"]["name"] = "missing"
    elif case == "boolean_comparison":
        draft["long"]["entry"]["rules"][1]["right"] = dict(kind="constant", value=True)
    elif case == "duplicate_feature":
        draft["features"] *= 2
    else:
        draft["take_profit"] = dict(kind="risk_reward", multiple="2")
        wire["evidence"].append(dict(field="take_profit", quote=TEXT))
    StructuredInterpretation.model_validate_json(json.dumps(wire))
    with pytest.raises(InterpretedStrategyError) as caught:
        run(wire)
    assert str(caught.value) == InterpretedStrategyError.message


@pytest.mark.parametrize("case", ["bounds", "wrong_default_type", "unsupported_operator", "code", "future_offset"])
def test_nested_phase5_contracts_remain_strict(case):
    wire = ready()
    if case == "bounds":
        wire["draft"]["parameters"][0]["maximum"] = 99
    elif case == "wrong_default_type":
        wire["draft"]["parameters"][0]["default"] = True
    elif case == "unsupported_operator":
        wire["draft"]["long"]["entry"]["rules"][0]["comparison"] = "approximately"
    elif case == "code":
        wire["draft"]["long"]["entry"]["rules"][0]["left"] = dict(kind="code", expression="exec(x)")
    else:
        wire["draft"]["long"]["entry"]["rules"][0]["left"]["offset"] = -1
    with pytest.raises(llm.StructuredOutputError):
        run(wire)


@pytest.mark.parametrize("case", ["missing", "fabricated_quote", "irrelevant_field"])
def test_evidence_binding_fails_closed(case):
    wire = ready()
    if case == "missing":
        wire["evidence"].pop()
    elif case == "fabricated_quote":
        wire["evidence"][0]["quote"] = "The user specified something else"
    else:
        wire["evidence"].append(dict(field="short", quote=TEXT))
    with pytest.raises(InterpretationContractError):
        run(wire)


def test_a_b_a_state_isolation_and_deterministic_conversion():
    async def scenario():
        fake = llm.FakeProvider(INFO, (raw(), raw(clarification()), raw()))
        client = llm.LLMClient(fake)
        a = await interpret_strategy(client, source(), identity=IDENTITY)
        b = await interpret_strategy(client, source(strategy_text="Buy SMA"), identity=IDENTITY)
        replay = await interpret_strategy(client, source(), identity=IDENTITY)
        assert a == replay and a.proposal.content_digest == replay.proposal.content_digest
        assert b.proposal is None
        assert fake.history[0] == fake.history[2] != fake.history[1]
    asyncio.run(scenario())


@pytest.mark.parametrize("error,attempts", [(llm.TransientProviderError, 2),
    (llm.RateLimitError, 2), (llm.ProviderTimeoutError, 2), (llm.ProviderError, 1)])
def test_provider_retry_policy_owned_by_phase13(error, attempts):
    fake = llm.FakeProvider(INFO, (error(), raw()))
    client = llm.LLMClient(fake, policy=llm.InvocationPolicy(max_attempts=2))
    if attempts == 1:
        with pytest.raises(llm.ProviderError):
            asyncio.run(interpret_strategy(client, source(), identity=IDENTITY))
    else:
        result = asyncio.run(interpret_strategy(client, source(), identity=IDENTITY))
        assert result.invocation.attempts == 2
    assert len(fake.history) == attempts


def test_exhausted_provider_failure_does_not_add_retry_loop():
    fake = llm.FakeProvider(INFO, (llm.RateLimitError(), llm.RateLimitError(), raw()))
    with pytest.raises(llm.RateLimitError):
        asyncio.run(interpret_strategy(llm.LLMClient(fake, policy=llm.InvocationPolicy(max_attempts=2)),
                                      source(), identity=IDENTITY))
    assert len(fake.history) == 2


def test_capability_and_budget_errors_are_unchanged():
    info = INFO.model_copy(update={"capabilities": llm.Capabilities(structured_output=False)})
    fake = llm.FakeProvider(info, (raw(),))
    with pytest.raises(llm.UnsupportedCapabilityError):
        asyncio.run(interpret_strategy(llm.LLMClient(fake), source(), identity=IDENTITY))
    assert not fake.history
    fake = llm.FakeProvider(INFO, (raw(),))
    with pytest.raises(llm.BudgetExceededError):
        asyncio.run(interpret_strategy(llm.LLMClient(fake, policy=llm.InvocationPolicy(max_output_tokens=10)),
                                      source(), identity=IDENTITY))
    assert not fake.history
