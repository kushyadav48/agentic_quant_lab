import asyncio

from pydantic import BaseModel, ConfigDict
import pytest

from quantlab.llm import (
    FakeProvider, FinishReason, InvocationPolicy, LLMClient, LLMInputError,
    StructuredOutput, StructuredOutputError, generate_structured, structured_output,
    validate_structured,
)
from .helpers import Answer, INFO, request, response


def test_typed_structured_result_preserves_raw_output_and_schema():
    schema = structured_output(Answer)
    req = request(structured_output=schema)
    raw = response(text='{"count":2,"label":"hello"}', finish_reason=FinishReason.STOP)
    fake = FakeProvider(INFO, (raw,), expected_requests=(req,))
    value, result = asyncio.run(generate_structured(LLMClient(fake), req, Answer))
    assert type(value) is Answer and value == Answer(count=2, label="hello")
    assert result.response == raw and result.request.structured_output == schema
    assert "<class" not in req.model_dump_json()


@pytest.mark.parametrize("text", ["", "{", "```json\n{}\n```", "not JSON", "[]", "null", "1",
    '{"count":1}', '{"count":1,"label":"x","extra":1}',
    '{"count":"1","label":"x"}', '{"count":true,"label":"x"}',
    '{"count":1.0,"label":"x"}', '{"count":1,"label":2}',
    '{"count":NaN,"label":"x"}', '{"count":Infinity,"label":"x"}',
    '{"count":1,"count":2,"label":"x"}', '{"count":1,"label":"x"} trailing'])
def test_bad_json_and_schema_fail_without_repair_or_retry(text):
    req = request(structured_output=structured_output(Answer))
    raw = response(text=text)
    fake = FakeProvider(INFO, (raw, response(text='{"count":1,"label":"x"}')))
    with pytest.raises(StructuredOutputError) as caught:
        asyncio.run(generate_structured(LLMClient(fake, policy=InvocationPolicy(max_attempts=3)), req, Answer))
    assert caught.value.response == raw and len(fake.history) == 1
    assert caught.value.args == (StructuredOutputError.message,)


@pytest.mark.parametrize("finish", [FinishReason.LENGTH, FinishReason.REFUSAL, FinishReason.CONTENT_FILTER, FinishReason.OTHER])
def test_incomplete_or_refused_output_is_not_trusted_even_if_json_valid(finish):
    with pytest.raises(StructuredOutputError):
        validate_structured(response(text='{"count":1,"label":"x"}', finish_reason=finish), Answer)


@pytest.mark.parametrize("schema", [None, StructuredOutput(name="wrong", json_schema='{"type":"object"}')])
def test_expected_schema_must_match_before_provider_invocation(schema):
    fake = FakeProvider(INFO, (response(),))
    with pytest.raises(LLMInputError):
        asyncio.run(generate_structured(LLMClient(fake), request(structured_output=schema), Answer))
    assert fake.history == ()


def test_model_must_explicitly_forbid_extra_fields():
    class Loose(BaseModel):
        count: int

    for model in (Loose, object, Answer(count=1, label="x"), None):
        with pytest.raises(LLMInputError):
            structured_output(model)


def test_strict_json_rejects_overflow_and_nested_duplicate_keys():
    class Nested(BaseModel):
        model_config = ConfigDict(extra="forbid")
        values: dict[str, float]

    for text in ('{"values":{"a":1e999}}', '{"values":{"a":1,"a":2}}', '{"values":{"a":"1"}}'):
        with pytest.raises(StructuredOutputError):
            validate_structured(response(text=text), Nested)


def test_nested_model_constraints_are_preserved():
    class Envelope(BaseModel):
        model_config = ConfigDict(extra="forbid")
        answer: Answer

    value = validate_structured(response(text='{"answer":{"count":1,"label":"x"}}'), Envelope)
    assert type(value.answer) is Answer
    for text in ('{"answer":{"count":true,"label":"x"}}', '{"answer":{"count":1,"label":"x","extra":0}}'):
        with pytest.raises(StructuredOutputError):
            validate_structured(response(text=text), Envelope)


def test_raw_sensitive_content_is_available_only_by_explicit_access():
    raw = response(text="sentinel-sensitive-output")
    with pytest.raises(StructuredOutputError) as caught:
        validate_structured(raw, Answer)
    assert "sentinel-sensitive-output" not in str(caught.value) + repr(caught.value) + repr(raw)
    assert caught.value.response.text == "sentinel-sensitive-output"
