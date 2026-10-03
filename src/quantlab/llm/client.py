"""Bounded async invocation; no agent, quantitative authority, or global state."""
import asyncio
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from .errors import (
    BudgetExceededError, LLMInputError, ProviderError, ProviderTimeoutError,
    RateLimitError, ResponseCompatibilityError, TransientProviderError,
    UnsupportedCapabilityError, UsageUnavailableError,
)
from .models import (
    ImageReference, InvocationPolicy, InvocationResult, LLMRequest, LLMResponse,
    ProviderInfo, TextContent,
)
from .provider import LLMProvider

RetryDelay = Callable[[int], Awaitable[None]]


async def _delay(milliseconds: int) -> None:
    if milliseconds:
        await asyncio.sleep(milliseconds / 1000)


def _check_request(request: LLMRequest, info: ProviderInfo, policy: InvocationPolicy) -> None:
    if request.identity != info.identity:
        raise LLMInputError()
    caps = info.capabilities
    for message in request.messages:
        for content in message.content:
            if (isinstance(content, TextContent) and not caps.text_input
                    or isinstance(content, ImageReference) and not caps.image_input):
                raise UnsupportedCapabilityError()
    if request.structured_output is not None and not caps.structured_output:
        raise UnsupportedCapabilityError()
    if request.max_output_tokens > policy.max_output_tokens:
        raise BudgetExceededError()
    if policy.max_total_tokens is not None and request.max_output_tokens > policy.max_total_tokens:
        raise BudgetExceededError()


def _check_response(response: LLMResponse, request: LLMRequest, policy: InvocationPolicy) -> None:
    if response.identity != request.identity:
        raise ResponseCompatibilityError()
    usage = response.usage
    if usage is not None and usage.output_tokens is not None:
        if usage.output_tokens > request.max_output_tokens:
            raise ResponseCompatibilityError()
    if policy.max_total_tokens is not None:
        if usage is None:
            raise UsageUnavailableError()
        total = usage.total_tokens
        if total is None and usage.input_tokens is not None and usage.output_tokens is not None:
            total = usage.input_tokens + usage.output_tokens
        if total is None:
            raise UsageUnavailableError()
        if total > policy.max_total_tokens:
            raise BudgetExceededError()


class LLMClient:
    """One adapter, immutable policy, and local counters for each invocation."""

    def __init__(self, provider: LLMProvider, *, policy: InvocationPolicy = InvocationPolicy(),
                 retry_delay: RetryDelay = _delay) -> None:
        try:
            if not isinstance(policy, InvocationPolicy) or not callable(retry_delay):
                raise LLMInputError()
            self._policy = InvocationPolicy.model_validate(policy)
        except ValidationError:
            raise LLMInputError() from None
        self._provider = provider
        self._retry_delay = retry_delay

    async def generate(self, request: LLMRequest) -> InvocationResult:
        try:
            if not isinstance(request, LLMRequest):
                raise LLMInputError()
            request = LLMRequest.model_validate(request)
        except ValidationError:
            raise LLMInputError() from None
        try:
            info = self._provider.info
            if not isinstance(info, ProviderInfo):
                raise ResponseCompatibilityError()
            info = ProviderInfo.model_validate(info)
        except Exception:
            raise ResponseCompatibilityError() from None
        _check_request(request, info, self._policy)
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                if self._policy.timeout_ms is None:
                    response = await self._provider.generate(request)
                else:
                    async with asyncio.timeout(self._policy.timeout_ms / 1000):
                        response = await self._provider.generate(request)
            except (TransientProviderError, TimeoutError) as error:
                # Recreate neutral errors: never propagate arbitrary SDK text.
                error_type = (ProviderTimeoutError if isinstance(error, (TimeoutError, ProviderTimeoutError))
                              else RateLimitError if isinstance(error, RateLimitError)
                              else TransientProviderError)
                if attempt == self._policy.max_attempts:
                    raise error_type() from None
                await self._retry_delay(self._policy.retry_delay_ms)
                continue
            except ResponseCompatibilityError:
                raise ResponseCompatibilityError() from None
            except UnsupportedCapabilityError:
                raise UnsupportedCapabilityError() from None
            except Exception:
                # Cancellation (BaseException) intentionally propagates unchanged.
                raise ProviderError() from None
            try:
                if not isinstance(response, LLMResponse):
                    raise ResponseCompatibilityError()
                response = LLMResponse.model_validate(response)
            except ValidationError:
                raise ResponseCompatibilityError() from None
            _check_response(response, request, self._policy)
            return InvocationResult(request=request, response=response, policy=self._policy,
                                    attempts=attempt)
        raise AssertionError("validated attempt count must be positive")
