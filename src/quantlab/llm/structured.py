"""Typed structured output; provider text is always untrusted."""
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ._json import load_json
from .client import LLMClient
from .enums import FinishReason
from .errors import LLMInputError, StructuredOutputError
from .models import InvocationResult, LLMRequest, LLMResponse, StructuredOutput

ModelT = TypeVar("ModelT", bound=BaseModel)


def structured_output(model: type[BaseModel], *, name: str | None = None) -> StructuredOutput:
    """Transport a Pydantic object's schema without exposing the Python class.

    Callers must forbid extra fields; nested models retain their own constraints.
    Pydantic's strict JSON semantics permit JSON encodings of dates/enums/tuples.
    """
    try:
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise LLMInputError()
        if model.model_config.get("extra") != "forbid":
            raise LLMInputError()
        import json
        return StructuredOutput(name=model.__name__ if name is None else name,
                                json_schema=json.dumps(model.model_json_schema(), allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        raise LLMInputError() from None


def validate_structured(response: LLMResponse, model: type[ModelT]) -> ModelT:
    structured_output(model)
    try:
        if not isinstance(response, LLMResponse):
            raise LLMInputError()
        response = LLMResponse.model_validate(response)
    except ValidationError:
        raise LLMInputError() from None
    try:
        if response.finish_reason not in (None, FinishReason.STOP):
            raise ValueError("incomplete or refused response")
        load_json(response.text)
        return model.model_validate_json(response.text, strict=True)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise StructuredOutputError(response) from None


async def generate_structured(client: LLMClient, request: LLMRequest,
                              model: type[ModelT]) -> tuple[ModelT, InvocationResult]:
    """Validate the exact schema requested; return typed output and audit artifact.

    Request schema must be explicit. No silent conversion of a plain-text request.
    Parsing is outside the retry loop: invalid content never triggers a retry.
    """
    try:
        if not isinstance(request, LLMRequest):
            raise LLMInputError()
        request = LLMRequest.model_validate(request)
    except ValidationError:
        raise LLMInputError() from None
    if request.structured_output is None:
        raise LLMInputError()
    expected = structured_output(model, name=request.structured_output.name)
    if expected != request.structured_output:
        raise LLMInputError()
    result = await client.generate(request)
    return validate_structured(result.response, model), result
