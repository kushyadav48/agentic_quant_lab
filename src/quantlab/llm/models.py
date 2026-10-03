"""Strict immutable LLM contracts, independent of all quantitative domains."""
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator,
    model_validator,
)

from ._json import canonical_schema
from .enums import FinishReason, ImageMediaType, MessageRole

OpaqueID = Annotated[str, StringConstraints(min_length=1, max_length=512, pattern=r"^\S+\z")]
TokenCount = Annotated[int, Field(ge=0)]
TokenLimit = Annotated[int, Field(gt=0, le=1_000_000)]


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


Text = Annotated[str, StringConstraints(min_length=1, max_length=1_000_000),
                 AfterValidator(_nonblank)]


class _Contract(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", validate_default=True,
        revalidate_instances="always", hide_input_in_errors=True,
    )


class TextContent(_Contract):
    kind: Literal["text"] = "text"
    text: Text = Field(repr=False)


class ImageReference(_Contract):
    """Future vision input: caller-owned opaque asset reference, never fetched here.

    MIME is a declaration, not byte validation. Phase 15 owns upload validation.
    No URL, bytes, credentials, download or image interpretation is implemented.
    """
    kind: Literal["image"] = "image"
    asset_id: OpaqueID = Field(repr=False)
    media_type: ImageMediaType


Content = Annotated[TextContent | ImageReference, Field(discriminator="kind")]


class Message(_Contract):
    role: MessageRole
    content: tuple[Content, ...] = Field(min_length=1, max_length=128, repr=False)


class ModelIdentity(_Contract):
    provider_id: OpaqueID
    model_id: OpaqueID


class Capabilities(_Contract):
    text_input: bool = True
    image_input: bool = False
    structured_output: bool = False


class ProviderInfo(_Contract):
    identity: ModelIdentity
    capabilities: Capabilities = Capabilities()


class PromptProvenance(_Contract):
    prompt_id: OpaqueID
    prompt_version: OpaqueID
    input_reference: OpaqueID | None = None


class GenerationParameters(_Contract):
    temperature: Annotated[Decimal, Field(ge=0, le=2, allow_inf_nan=False)] | None = None
    top_p: Annotated[Decimal, Field(gt=0, le=1, allow_inf_nan=False)] | None = None
    seed: Annotated[int, Field(ge=0, le=2**63 - 1)] | None = None


class StructuredOutput(_Contract):
    """Immutable neutral schema text; adapters translate it to their wire format."""
    name: OpaqueID
    json_schema: Annotated[str, Field(min_length=2, max_length=1_000_000)] = Field(repr=False)

    @field_validator("json_schema")
    @classmethod
    def canonical(cls, value: str) -> str:
        try:
            return canonical_schema(value)
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise ValueError("invalid JSON Schema transport") from None


class LLMRequest(_Contract):
    identity: ModelIdentity
    messages: tuple[Message, ...] = Field(min_length=1, max_length=256, repr=False)
    provenance: PromptProvenance
    max_output_tokens: TokenLimit
    parameters: GenerationParameters = GenerationParameters()
    structured_output: StructuredOutput | None = None


class TokenUsage(_Contract):
    """Reported values only; unknown counts remain None, including total."""
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.total_tokens is not None:
            components = (self.input_tokens, self.output_tokens)
            if any(c is not None and c > self.total_tokens for c in components):
                raise ValueError("component exceeds reported total tokens")
            if all(c is not None for c in components):
                if self.input_tokens + self.output_tokens != self.total_tokens:
                    raise ValueError("reported token total must equal input plus output")
        return self


class LLMResponse(_Contract):
    identity: ModelIdentity
    text: Annotated[str, Field(max_length=4_000_000)] = Field(repr=False)
    finish_reason: FinishReason | None = None
    usage: TokenUsage | None = None
    provider_request_id: OpaqueID | None = None


class InvocationPolicy(_Contract):
    """Limits are per generate invocation; timeout applies to each attempt."""
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 1
    max_output_tokens: TokenLimit = 4096
    max_total_tokens: Annotated[int, Field(gt=0)] | None = None
    timeout_ms: Annotated[int, Field(gt=0, le=3_600_000)] | None = None
    retry_delay_ms: Annotated[int, Field(ge=0, le=60_000)] = 0


class InvocationResult(_Contract):
    request: LLMRequest = Field(repr=False)
    response: LLMResponse = Field(repr=False)
    policy: InvocationPolicy
    attempts: Annotated[int, Field(ge=1, le=10)]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.request.identity != self.response.identity:
            raise ValueError("invocation identity mismatch")
        if self.attempts > self.policy.max_attempts:
            raise ValueError("attempts exceed invocation policy")
        return self
