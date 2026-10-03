"""The only interface a future vendor adapter must implement."""
from typing import Protocol, runtime_checkable

from .models import LLMRequest, LLMResponse, ProviderInfo


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def info(self) -> ProviderInfo:
        """Explicit identity and capabilities for one configured model."""
        ...

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Return neutral output or raise a sanitized provider error.

        Runtime credentials belong to adapter construction, outside artifacts.
        Adapters must honor cancellation and translate SDK failures without
        copying SDK exception messages, headers, or credentials.
        """
        ...
