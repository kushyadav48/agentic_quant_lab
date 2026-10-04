import asyncio
import json

import pytest

import quantlab.llm as llm
from quantlab.interpretation import (
    InterpretedStrategyError, InterpretationContractError, InterpretationStatus,
    MultimodalInterpretation, interpret_multimodal_strategy,
)
from quantlab.strategies import ApprovalState, Origin, StrategySpecification, validate_content
from tests.llm.helpers import IDENTITY
from .multimodal_helpers import (
    INFO, clarification, conflict, raw, ready, run, source, visual, visual_ready,
)


@pytest.mark.parametrize("images_only", [False, True])
def test_ready_uses_existing_strategy_contracts(images_only):
    value = source(strategy_text=None) if images_only else source()
    result, fake = run(visual_ready() if images_only else ready(), input=value)
    spec = result.proposal
    assert type(spec) is StrategySpecification
    assert spec.state is ApprovalState.DRAFT and spec.approval is None and spec.created_at is None
    assert (spec.strategy_id, spec.version) == (value.strategy_id, value.version)
    assert spec.content.features == result.interpretation.draft.features
    assert spec.content.long == result.interpretation.draft.long
    assert spec.content.provenance.origin is Origin.CHART_MULTIMODAL
    assert spec.content.provenance.source_reference == value.input_reference
    assert spec.content.provenance.notes == "Interpretation prompt quantlab.multimodal-strategy version 1"
    validate_content(spec.content)
    assert spec.content_digest == spec.content.content_digest()
    assert result.invocation.request == fake.history[0]
    assert result.invocation.response == raw(visual_ready() if images_only else ready())


@pytest.mark.parametrize("ambiguity,question", [
    ("SMA period unreadable", "Which SMA period?"),
    ("No visible timeframe", "Which timeframe?"),
    ("Instrument is ambiguous", "Which instrument reference?"),
    ("Entry marked but no exit", "What exit or explicit holding policy?"),
    ("Multiple lines have no labels", "What does each line represent?"),
    ("A horizontal level is drawn", "What exact entry and exit comparisons are intended?"),
    ("ATR is unsupported", "Would you supply a supported rule?"),
    ("MACD is unsupported", "Would you supply a supported rule?"),
    ("Bollinger Bands are unsupported", "Would you supply a supported rule?"),
    ("Pattern rule is unclear", "What supported declarative rules express this pattern?"),
    ("Exact value is ambiguous", "What explicit threshold should be used?"),
    ("Image has no interpretable strategy intent", "What strategy do you intend?"),
    ("Conflicting symbols inside the screenshot", "Which symbol is intended?"),
])
def test_clarification_has_questions_and_no_proposal(ambiguity, question):
    result, _ = run(clarification(ambiguity, question), input=source(strategy_text=None))
    assert result.status is InterpretationStatus.NEEDS_CLARIFICATION
    assert result.proposal is None and result.interpretation.draft is None
    assert result.interpretation.clarifications[0].question == question


@pytest.mark.parametrize("field,text,observation", [
    ("instruments", "Use EUR/USD", "The symbol appears to be GBP/USD."),
    ("timeframe", "Use 15m", "The timeframe appears to be 1h."),
    ("features", "Use SMA20", "The indicator label appears to say SMA50."),
    ("direction", "Trade long", "The annotation appears to say short."),
])
def test_text_image_conflict_requires_clarification(field, text, observation):
    wire = conflict(field, text, observation)
    result, _ = run(wire, input=source(strategy_text=text))
    assert result.proposal is None
    assert result.interpretation.conflicts[0].field == field
    # Even a complete otherwise valid draft cannot override a reported conflict.
    forged_ready = ready() | dict(conflicts=wire["conflicts"])
    with pytest.raises(llm.StructuredOutputError):
        run(forged_ready, input=source(strategy_text=text))


def test_conflicting_images_cannot_be_silently_selected():
    wire = conflict(images_only=True)
    result, _ = run(wire, input=source(strategy_text=None))
    assert result.proposal is None and len(result.interpretation.conflicts) == 1
    with pytest.raises(llm.StructuredOutputError):
        run(visual_ready() | dict(conflicts=wire["conflicts"]), input=source(strategy_text=None))


@pytest.mark.parametrize("case", ["missing_field", "fabricated_asset", "fabricated_quote", "extra_field",
    "text_without_input", "conflict_asset", "conflict_quote"])
def test_evidence_binding(case):
    wire, value = visual_ready(), source()
    if case == "missing_field":
        wire["visual_evidence"].pop()
    elif case == "fabricated_asset":
        wire["visual_evidence"][0]["asset_id"] = "chart:invented"
    elif case == "extra_field":
        wire["visual_evidence"].append(visual("short"))
    elif case in ("fabricated_quote", "text_without_input"):
        wire = ready()
        if case == "fabricated_quote":
            wire["evidence"][0]["quote"] = "not actually supplied"
        else:
            value = source(strategy_text=None)
    else:
        wire = conflict(quote="Trade")
        if case == "conflict_asset":
            wire["conflicts"][0]["visual_evidence"][0]["asset_id"] = "chart:invented"
        else:
            wire["conflicts"][0]["text_evidence"][0]["quote"] = "not actually supplied"
    with pytest.raises(InterpretationContractError):
        run(wire, input=value)


def test_evidence_is_review_aid_not_a_semantic_truth_or_pixel_verifier():
    wire = visual_ready()
    wire["visual_evidence"][0]["observation"] = "A blue line is visible."
    result, _ = run(wire, input=source(strategy_text=None))
    # Binding/coverage is enforceable; relevance and visual truth need human review.
    assert result.proposal.content.instruments == ("research:TEST",)
    assert result.proposal.approval is None


@pytest.mark.parametrize("text", ["", "{", "[]", "null", "1", "{}", "not JSON",
    '```json\n{}\n```', '{"status":"READY","status":"NEEDS_CLARIFICATION"}',
    '{"status":NaN}', '{"status":Infinity}', json.dumps(ready()) + " trailing",
    json.dumps(ready()).replace('"period", "value": 2', '"period", "value": 2, "value": 3')])
def test_invalid_structured_output_has_no_repair_or_retry(text):
    fake = llm.FakeProvider(INFO, (raw(text=text), raw()))
    with pytest.raises(llm.StructuredOutputError) as caught:
        asyncio.run(interpret_multimodal_strategy(llm.LLMClient(fake,
            policy=llm.InvocationPolicy(max_attempts=3)), source(), identity=IDENTITY))
    assert len(fake.history) == 1
    assert str(caught.value) == llm.StructuredOutputError.message


@pytest.mark.parametrize("finish", [llm.FinishReason.LENGTH, llm.FinishReason.REFUSAL,
    llm.FinishReason.CONTENT_FILTER, llm.FinishReason.OTHER])
def test_invalid_finish_reason(finish):
    fake = llm.FakeProvider(INFO, (raw(finish_reason=finish),))
    with pytest.raises(llm.StructuredOutputError):
        asyncio.run(interpret_multimodal_strategy(llm.LLMClient(fake), source(), identity=IDENTITY))


@pytest.mark.parametrize("field,value", [("approval", {}), ("approved", True), ("state", "approved"),
    ("strategy_id", "model_chosen"), ("version", 99), ("created_at", "today"),
    ("provenance", {}), ("direction", 1), ("features", "sma(2)"), ("ohlc", [])])
def test_model_cannot_add_authority_or_wrong_types(field, value):
    wire = ready()
    wire["draft"][field] = value
    with pytest.raises(llm.StructuredOutputError):
        run(wire)


@pytest.mark.parametrize("case", ["direction", "missing_feature", "missing_parameter", "boolean_comparison",
    "duplicate_feature", "risk_reward_without_stop", "unsupported_feature", "bad_period"])
def test_shape_valid_output_uses_phase5_validation_and_phase14_vocabulary(case):
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
    elif case == "risk_reward_without_stop":
        draft["take_profit"] = dict(kind="risk_reward", multiple="2")
        wire["visual_evidence"].append(visual("take_profit"))
    elif case == "unsupported_feature":
        draft["features"][0]["implementation_id"] = "atr"
    else:
        draft["features"][0]["parameters"][0]["value"] = True
    MultimodalInterpretation.model_validate_json(json.dumps(wire))
    with pytest.raises(InterpretedStrategyError):
        run(wire)


@pytest.mark.parametrize("case", ["parameter_type", "parameter_bounds", "operator", "future_offset", "code"])
def test_nested_phase5_types_remain_strict(case):
    wire = ready()
    draft = wire["draft"]
    if case == "parameter_type":
        draft["parameters"][0]["default"] = True
    elif case == "parameter_bounds":
        draft["parameters"][0]["maximum"] = 99
    elif case == "operator":
        draft["long"]["entry"]["rules"][0]["comparison"] = "approximately"
    elif case == "future_offset":
        draft["long"]["entry"]["rules"][0]["left"]["offset"] = -1
    else:
        draft["long"]["entry"]["rules"][0]["left"] = dict(kind="code", expression="exec(x)")
    with pytest.raises(llm.StructuredOutputError):
        run(wire)


@pytest.mark.parametrize("capability", ["image_input", "structured_output", "text_input"])
def test_capabilities_fail_before_provider_invocation(capability):
    caps = dict(image_input=True, structured_output=True, text_input=True) | {capability: False}
    fake = llm.FakeProvider(llm.ProviderInfo(identity=IDENTITY, capabilities=llm.Capabilities(**caps)), (raw(),))
    with pytest.raises(llm.UnsupportedCapabilityError):
        asyncio.run(interpret_multimodal_strategy(llm.LLMClient(fake), source(), identity=IDENTITY))
    assert fake.history == ()


def test_a_b_a_state_isolation():
    async def scenario():
        fake = llm.FakeProvider(INFO, (raw(), raw(clarification()), raw()))
        client = llm.LLMClient(fake)
        a = await interpret_multimodal_strategy(client, source(), identity=IDENTITY)
        b = await interpret_multimodal_strategy(client, source(strategy_text=None), identity=IDENTITY)
        replay = await interpret_multimodal_strategy(client, source(), identity=IDENTITY)
        assert a == replay and a.proposal.content_digest == replay.proposal.content_digest
        assert b.proposal is None
        assert fake.history[0] == fake.history[2] != fake.history[1]
    asyncio.run(scenario())


@pytest.mark.parametrize("error,attempts", [(llm.TransientProviderError, 2),
    (llm.ProviderTimeoutError, 2), (llm.RateLimitError, 2), (llm.ProviderError, 1)])
def test_only_phase13_retries_providers(error, attempts):
    fake = llm.FakeProvider(INFO, (error(), raw()))
    client = llm.LLMClient(fake, policy=llm.InvocationPolicy(max_attempts=2))
    if attempts == 1:
        with pytest.raises(llm.ProviderError):
            asyncio.run(interpret_multimodal_strategy(client, source(), identity=IDENTITY))
    else:
        result = asyncio.run(interpret_multimodal_strategy(client, source(), identity=IDENTITY))
        assert result.invocation.attempts == attempts
    assert len(fake.history) == attempts


def test_provider_exception_text_is_sanitized_and_budget_preflight_is_reused():
    error = llm.ProviderError()
    error.args = ("sensitive provider detail",)
    fake = llm.FakeProvider(INFO, (error,))
    with pytest.raises(llm.ProviderError) as caught:
        asyncio.run(interpret_multimodal_strategy(llm.LLMClient(fake), source(), identity=IDENTITY))
    assert str(caught.value) == llm.ProviderError.message
    fake = llm.FakeProvider(INFO, (raw(),))
    with pytest.raises(llm.BudgetExceededError):
        asyncio.run(interpret_multimodal_strategy(llm.LLMClient(fake,
            policy=llm.InvocationPolicy(max_output_tokens=10)), source(), identity=IDENTITY))
    assert not fake.history
