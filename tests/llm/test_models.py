from decimal import Decimal as D, localcontext, Inexact
import json

from pydantic import ValidationError
import pytest

from quantlab.llm import (
    Capabilities, GenerationParameters, ImageMediaType, ImageReference,
    InvocationPolicy, InvocationResult, LLMRequest, Message, MessageRole,
    ModelIdentity, PromptProvenance, StructuredOutput, TextContent, TokenUsage,
    structured_output,
)
from .helpers import Answer, IDENTITY, INFO, request, response


@pytest.mark.parametrize("value", [True, False, -1, 0, 1.0, "1", None, 1_000_001])
def test_strict_output_token_limit(value):
    with pytest.raises(ValidationError):
        request(max_output_tokens=value)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "total_tokens"])
@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1"])
def test_strict_usage_counts(field, value):
    with pytest.raises(ValidationError):
        TokenUsage(**{field: value})


@pytest.mark.parametrize("values", [dict(input_tokens=2, total_tokens=1),
    dict(output_tokens=2, total_tokens=1), dict(input_tokens=1, output_tokens=2, total_tokens=4)])
def test_incoherent_usage(values):
    with pytest.raises(ValidationError):
        TokenUsage(**values)


def test_usage_unknown_and_zero_are_distinct():
    assert TokenUsage().model_dump() == dict(input_tokens=None, output_tokens=None, total_tokens=None)
    assert TokenUsage(input_tokens=0, output_tokens=0).total_tokens is None
    assert TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3).total_tokens == 3
    assert response().usage is None
    assert response().finish_reason is None
    assert response().provider_request_id is None


@pytest.mark.parametrize("field", ["temperature", "top_p"])
@pytest.mark.parametrize("value", [True, 1, 0.5, "0.5", D("NaN"), D("Infinity"), D("-Infinity"), D("-0.1")])
def test_strict_generation_values(field, value):
    with pytest.raises(ValidationError):
        GenerationParameters(**{field: value})


@pytest.mark.parametrize("values", [dict(temperature=D("2.01")), dict(top_p=D(0)),
    dict(top_p=D("1.01")), dict(seed=True), dict(seed=-1), dict(seed=2**63)])
def test_generation_bounds(values):
    with pytest.raises(ValidationError):
        GenerationParameters(**values)


@pytest.mark.parametrize("field", ["provider_id", "model_id"])
@pytest.mark.parametrize("value", ["", " ", "has space", "trailing\n", "x" * 513, 1, True])
def test_opaque_identity_validation(field, value):
    with pytest.raises(ValidationError):
        ModelIdentity(**(IDENTITY.model_dump() | {field: value}))


def test_opaque_identities_are_not_normalized():
    assert ModelIdentity(provider_id="Vendor-X", model_id="A/B:2026").model_id == "A/B:2026"
    assert ModelIdentity(provider_id="Vendor-X", model_id="A") != ModelIdentity(provider_id="vendor-x", model_id="A")


@pytest.mark.parametrize("values", [dict(messages=()), dict(messages=[]), dict(messages=("x",)),
    dict(parameters={"temperature": True}), dict(provenance=PromptProvenance.model_construct(prompt_id="", prompt_version="v1"))])
def test_invalid_request_structure(values):
    with pytest.raises(ValidationError):
        request(**values)


@pytest.mark.parametrize("values", [dict(role="tool", content=(TextContent(text="x"),)),
    dict(role=MessageRole.USER, content=()), dict(role=MessageRole.USER, content=[]),
    dict(role=MessageRole.USER, content=({"kind": "python", "code": "print(1)"},))])
def test_message_contract(values):
    with pytest.raises(ValidationError):
        Message(**values)


@pytest.mark.parametrize("text", ["", " ", "\n\t", 1, b"text",
    pytest.param("x" * 1_000_001, id="oversized")])
def test_text_validation(text):
    with pytest.raises(ValidationError):
        TextContent(text=text)


@pytest.mark.parametrize("values", [dict(max_attempts=True), dict(max_attempts=0), dict(max_attempts=11),
    dict(max_total_tokens=0), dict(max_total_tokens=True), dict(timeout_ms=0), dict(timeout_ms=True),
    dict(timeout_ms=3_600_001), dict(retry_delay_ms=-1), dict(retry_delay_ms=60_001),
    dict(retry_delay_ms=1.0), dict(max_output_tokens=0)])
def test_policy_validation(values):
    with pytest.raises(ValidationError):
        InvocationPolicy(**values)


def test_message_and_content_order_are_contractual():
    messages = tuple(Message(role=role, content=(TextContent(text=role.value), TextContent(text="second")))
                     for role in MessageRole)
    req = request(messages=messages)
    assert req.messages == messages
    assert LLMRequest.model_validate_json(req.model_dump_json()) == req
    assert request(messages=tuple(reversed(messages))) != req


ARTIFACTS = [IDENTITY, INFO, Capabilities(), TextContent(text="hello"),
    ImageReference(asset_id="asset:1", media_type=ImageMediaType.PNG),
    Message(role=MessageRole.USER, content=(TextContent(text="hi"),)),
    PromptProvenance(prompt_id="test", prompt_version="1", input_reference="input:1"),
    GenerationParameters(temperature=D("0.10"), top_p=D(1), seed=0),
    structured_output(Answer), request(), response(), TokenUsage(input_tokens=2),
    InvocationPolicy(), InvocationResult(request=request(), response=response(), policy=InvocationPolicy(), attempts=1)]


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_all_public_contracts_roundtrip_frozen_and_extra_forbidden(artifact):
    cls = type(artifact)
    assert cls.model_validate_json(artifact.model_dump_json()) == artifact
    assert cls.model_validate(artifact) == artifact
    field = next(iter(cls.model_fields))
    with pytest.raises(ValidationError):
        setattr(artifact, field, getattr(artifact, field))
    with pytest.raises(ValidationError):
        cls.model_validate(artifact.model_dump() | {"api_key": "sentinel-secret"})


@pytest.mark.parametrize("value", ["{}", '{"type":"object","properties":{"x":{"type":"integer"}}}'])
def test_schema_canonicalization(value):
    obj = json.loads(value)
    a = StructuredOutput(name="test", json_schema=value)
    b = StructuredOutput(name="test", json_schema=json.dumps(obj, indent=4, sort_keys=True))
    assert a == b
    obj["title"] = "changed"
    assert json.loads(a.json_schema) != obj


@pytest.mark.parametrize("value", ["", "[]", "null", "false", "{", '{"x":NaN}', '{"x":1e999}',
    '{"x":1,"x":2}', '{"x":{"a":1,"a":2}}'])
def test_schema_invalid_json(value):
    with pytest.raises(ValidationError):
        StructuredOutput(name="test", json_schema=value)


def test_image_media_and_deep_revalidation():
    with pytest.raises(ValidationError):
        ImageReference(asset_id="image", media_type="text/html")
    with pytest.raises(ValidationError):
        request(messages=(Message.model_construct(role=MessageRole.USER,
                content=(TextContent.model_construct(text=""),)),))
    with pytest.raises(ValidationError):
        Capabilities(text_input=1)


def test_decimal_context_does_not_change_generation_or_serialization():
    parameters = GenerationParameters(temperature=D("0.125"), top_p=D("0.75"))
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        assert GenerationParameters.model_validate_json(parameters.model_dump_json()) == parameters


def test_provenance_affects_request_identity_without_ephemeral_fields():
    assert request() == request()
    assert request(provenance=PromptProvenance(prompt_id="test-prompt", prompt_version="v2")) != request()
    assert "timestamp" not in request().model_dump_json()


def test_invocation_artifacts_reject_identity_and_attempt_mismatches():
    with pytest.raises(ValidationError):
        InvocationResult(request=request(), response=response(identity=ModelIdentity(provider_id="other", model_id="other")),
                         policy=InvocationPolicy(), attempts=1)
    with pytest.raises(ValidationError):
        InvocationResult(request=request(), response=response(), policy=InvocationPolicy(), attempts=2)
