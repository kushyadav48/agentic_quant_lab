"""Stable, sanitized errors; never interpolate SDK messages or input content."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import LLMResponse


class LLMError(Exception):
    message = "LLM invocation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class LLMInputError(LLMError):
    message = "invalid local LLM input"


class UnsupportedCapabilityError(LLMError):
    message = "provider does not support a required capability"


class ProviderError(LLMError):
    message = "provider invocation failed"


class TransientProviderError(ProviderError):
    message = "transient provider failure"


class RateLimitError(TransientProviderError):
    message = "provider rate limit reached"


class ProviderTimeoutError(TransientProviderError):
    message = "provider attempt timed out"


class ResponseCompatibilityError(LLMError):
    message = "provider response is incompatible with the invocation"


class StructuredOutputError(LLMError):
    message = "provider output failed strict structured validation"

    def __init__(self, response: "LLMResponse") -> None:
        super().__init__()
        # Deliberate diagnostic access, excluded from exception text and repr.
        self.response = response


class BudgetExceededError(LLMError):
    message = "LLM invocation exceeds resource policy"


class UsageUnavailableError(LLMError):
    message = "reported usage is insufficient to enforce token policy"


class FakeScriptError(LLMError):
    message = "fake provider script exhausted or request mismatch"
