from pydantic import BaseModel, ConfigDict

from quantlab.llm import (
    Capabilities, LLMRequest, LLMResponse, Message, MessageRole, ModelIdentity,
    PromptProvenance, ProviderInfo, TextContent,
)

IDENTITY = ModelIdentity(provider_id="test.vendor/local", model_id="opaque:model-v1")
INFO = ProviderInfo(identity=IDENTITY, capabilities=Capabilities(structured_output=True))


class Answer(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    count: int
    label: str


def request(**changes):
    values = dict(identity=IDENTITY, messages=(Message(role=MessageRole.USER,
                  content=(TextContent(text="Explain this test input."),)),),
                  provenance=PromptProvenance(prompt_id="test-prompt", prompt_version="v1"),
                  max_output_tokens=32)
    return LLMRequest(**(values | changes))


def response(**changes):
    return LLMResponse(**(dict(identity=IDENTITY, text="test output") | changes))
