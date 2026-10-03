"""Offline scripted adapter with explicit, instance-scoped invocation history."""
from .errors import FakeScriptError, LLMError, LLMInputError
from .models import LLMRequest, LLMResponse, ProviderInfo


class FakeProvider:
    """Consume one response/error per attempt, optionally checking exact requests.

    Responses may intentionally be model_construct/model_copy for adversarial
    client tests. Script order is call order; no timestamps, network or randomness.
    """

    def __init__(self, info: ProviderInfo, script: tuple[LLMResponse | LLMError, ...], *,
                 expected_requests: tuple[LLMRequest, ...] | None = None) -> None:
        if not isinstance(info, ProviderInfo):
            raise LLMInputError()
        self._info = ProviderInfo.model_validate(info)
        if type(script) is not tuple or any(not isinstance(s, (LLMResponse, LLMError)) for s in script):
            raise LLMInputError()
        if expected_requests is not None:
            if type(expected_requests) is not tuple or len(expected_requests) != len(script):
                raise LLMInputError()
            if any(not isinstance(r, LLMRequest) for r in expected_requests):
                raise LLMInputError()
            expected_requests = tuple(LLMRequest.model_validate(r) for r in expected_requests)
        self._script = script
        self._expected_requests = expected_requests
        self._history: list[LLMRequest] = []

    @property
    def info(self) -> ProviderInfo:
        return self._info

    @property
    def history(self) -> tuple[LLMRequest, ...]:
        return tuple(self._history)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise LLMInputError()
        request = LLMRequest.model_validate(request)
        index = len(self._history)
        self._history.append(request)
        if index >= len(self._script):
            raise FakeScriptError()
        if self._expected_requests is not None and request != self._expected_requests[index]:
            raise FakeScriptError()
        step = self._script[index]
        if isinstance(step, LLMError):
            raise step
        return step
