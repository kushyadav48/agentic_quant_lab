import asyncio

import pytest

from quantlab.llm import (
    BudgetExceededError, Capabilities, FakeProvider, FakeScriptError, ImageMediaType,
    ImageReference, InvocationPolicy, LLMClient, LLMInputError, LLMProvider, Message,
    MessageRole, ModelIdentity, ProviderError, ProviderInfo, ProviderTimeoutError,
    RateLimitError, ResponseCompatibilityError, TokenUsage, TransientProviderError,
    UnsupportedCapabilityError, UsageUnavailableError, structured_output,
)
from .helpers import Answer, IDENTITY, INFO, request, response


def invoke(fake, req=None, **kwargs):
    return asyncio.run(LLMClient(fake, **kwargs).generate(request() if req is None else req))


def test_text_protocol_success_and_history_snapshot():
    fake = FakeProvider(INFO, (response(),), expected_requests=(request(),))
    assert isinstance(fake, LLMProvider)
    old_history = fake.history
    result = invoke(fake)
    assert result.request == request() and result.response == response() and result.attempts == 1
    assert fake.history == (request(),) and old_history == ()


@pytest.mark.parametrize("kind", ["text", "image", "structured"])
def test_capability_failure_has_no_attempts(kind):
    caps = Capabilities(text_input=kind != "text")
    req = request()
    if kind == "image":
        req = request(messages=(Message(role=MessageRole.USER, content=(
            ImageReference(asset_id="image:1", media_type=ImageMediaType.PNG),)),))
    elif kind == "structured":
        req = request(structured_output=structured_output(Answer))
    fake = FakeProvider(ProviderInfo(identity=IDENTITY, capabilities=caps), (response(),))
    with pytest.raises(UnsupportedCapabilityError):
        invoke(fake, req)
    assert fake.history == ()


def test_declared_image_contract_is_forwarded_without_loading_assets():
    req = request(messages=(Message(role=MessageRole.USER, content=(
        ImageReference(asset_id="missing:opaque-asset", media_type=ImageMediaType.WEBP),)),))
    fake = FakeProvider(ProviderInfo(identity=IDENTITY, capabilities=Capabilities(image_input=True)),
                        (response(),), expected_requests=(req,))
    assert invoke(fake, req).request == req


@pytest.mark.parametrize("error", [TransientProviderError, RateLimitError, ProviderTimeoutError])
def test_transient_retry_then_success_with_injected_delay(error):
    delays = []

    async def delay(milliseconds):
        delays.append(milliseconds)

    fake = FakeProvider(INFO, (error(), response()), expected_requests=(request(), request()))
    result = invoke(fake, policy=InvocationPolicy(max_attempts=3, retry_delay_ms=7), retry_delay=delay)
    assert result.attempts == 2 and delays == [7]
    assert fake.history == (request(), request())


@pytest.mark.parametrize("maximum", [1, 2, 10])
def test_attempt_limit_exact_and_no_delay_after_final_failure(maximum):
    delays = []

    async def delay(milliseconds):
        delays.append(milliseconds)

    fake = FakeProvider(INFO, tuple(RateLimitError() for _ in range(maximum + 1)))
    with pytest.raises(RateLimitError):
        invoke(fake, policy=InvocationPolicy(max_attempts=maximum), retry_delay=delay)
    assert len(fake.history) == maximum and len(delays) == maximum - 1


@pytest.mark.parametrize("error", [ProviderError, ResponseCompatibilityError, UnsupportedCapabilityError])
def test_permanent_errors_not_retried(error):
    fake = FakeProvider(INFO, (error(), response()))
    with pytest.raises(error):
        invoke(fake, policy=InvocationPolicy(max_attempts=3))
    assert len(fake.history) == 1


@pytest.mark.parametrize("req", [None, {}, request().model_copy(update={"max_output_tokens": True}),
    request(identity=ModelIdentity(provider_id="wrong", model_id="wrong"))])
def test_invalid_local_request_zero_attempts(req):
    fake = FakeProvider(INFO, (response(),))
    with pytest.raises(LLMInputError):
        asyncio.run(LLMClient(fake).generate(req))
    assert fake.history == ()


@pytest.mark.parametrize("policy", [None, {}, InvocationPolicy.model_construct(max_attempts=0)])
def test_forged_policy_rejected(policy):
    with pytest.raises(LLMInputError):
        LLMClient(FakeProvider(INFO, ()), policy=policy)


@pytest.mark.parametrize("limit", [31, 32, 33])
def test_requested_output_cap_boundary(limit):
    fake = FakeProvider(INFO, (response(),))
    if limit > 32:
        with pytest.raises(BudgetExceededError):
            invoke(fake, request(max_output_tokens=limit), policy=InvocationPolicy(max_output_tokens=32))
        assert fake.history == ()
    else:
        assert invoke(fake, request(max_output_tokens=limit), policy=InvocationPolicy(max_output_tokens=32)).attempts == 1


@pytest.mark.parametrize("total", [63, 64, 65])
def test_reported_total_budget_boundary(total):
    fake = FakeProvider(INFO, (response(usage=TokenUsage(total_tokens=total)), response()))
    policy = InvocationPolicy(max_total_tokens=64, max_attempts=2)
    if total > 64:
        with pytest.raises(BudgetExceededError):
            invoke(fake, policy=policy)
    else:
        assert invoke(fake, policy=policy).response.usage.total_tokens == total
    assert len(fake.history) == 1


@pytest.mark.parametrize("usage", [None, TokenUsage(), TokenUsage(input_tokens=1), TokenUsage(output_tokens=1)])
def test_missing_usage_fails_closed_only_when_budget_requires_it(usage):
    assert invoke(FakeProvider(INFO, (response(usage=usage),))).response.usage == usage
    fake = FakeProvider(INFO, (response(usage=usage), response()))
    with pytest.raises(UsageUnavailableError):
        invoke(fake, policy=InvocationPolicy(max_total_tokens=64, max_attempts=2))
    assert len(fake.history) == 1


def test_component_sum_enforces_budget_without_fabricating_total():
    result = invoke(FakeProvider(INFO, (response(usage=TokenUsage(input_tokens=32, output_tokens=32)),)),
                    policy=InvocationPolicy(max_total_tokens=64))
    assert result.response.usage.total_tokens is None
    with pytest.raises(BudgetExceededError):
        invoke(FakeProvider(INFO, (response(usage=TokenUsage(input_tokens=33, output_tokens=32)),)),
               policy=InvocationPolicy(max_total_tokens=64))


def test_requested_output_cannot_exceed_total_budget():
    fake = FakeProvider(INFO, (response(),))
    with pytest.raises(BudgetExceededError):
        invoke(fake, policy=InvocationPolicy(max_total_tokens=31))
    assert not fake.history


@pytest.mark.parametrize("bad", [
    response(identity=ModelIdentity(provider_id="wrong", model_id=IDENTITY.model_id)),
    response(identity=ModelIdentity(provider_id=IDENTITY.provider_id, model_id="wrong")),
    response(usage=TokenUsage(output_tokens=33)),
    response().model_copy(update={"text": 1}),
    response().model_copy(update={"usage": TokenUsage.model_construct(input_tokens=True)}),
    response().model_copy(update={"usage": TokenUsage.model_construct(input_tokens=2, output_tokens=2, total_tokens=1)}),
])
def test_response_incompatibility_is_not_retried(bad):
    fake = FakeProvider(INFO, (bad, response()))
    with pytest.raises(ResponseCompatibilityError):
        invoke(fake, policy=InvocationPolicy(max_attempts=2))
    assert len(fake.history) == 1


def test_raw_adapter_failures_and_return_types_are_sanitized():
    class Broken:
        info = INFO

        async def generate(self, request):
            raise RuntimeError("sentinel-secret from SDK")

    with pytest.raises(ProviderError) as caught:
        invoke(Broken())
    assert "sentinel-secret" not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__

    class WrongType:
        info = INFO

        async def generate(self, request):
            return {"text": "untyped"}

    with pytest.raises(ResponseCompatibilityError):
        invoke(WrongType())


def test_bad_provider_metadata_fails_before_generate():
    class Broken:
        info = INFO.model_copy(update={"capabilities": Capabilities.model_construct(text_input=1)})

        async def generate(self, request):
            raise AssertionError("must not invoke")

    with pytest.raises(ResponseCompatibilityError):
        invoke(Broken())


def test_timeout_policy_is_per_attempt_and_no_real_wait(monkeypatch):
    deadlines = []

    class Deadline:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            raise TimeoutError()

    def timeout(seconds):
        deadlines.append(seconds)
        return Deadline()

    monkeypatch.setattr(asyncio, "timeout", timeout)
    fake = FakeProvider(INFO, (response(), response()))
    with pytest.raises(ProviderTimeoutError):
        invoke(fake, policy=InvocationPolicy(timeout_ms=10, max_attempts=2))
    assert deadlines == [0.01, 0.01] and len(fake.history) == 2


def test_external_cancellation_propagates_without_retry():
    class Cancelled:
        info = INFO
        calls = 0

        async def generate(self, request):
            self.calls += 1
            raise asyncio.CancelledError()

    provider = Cancelled()
    with pytest.raises(asyncio.CancelledError):
        invoke(provider, policy=InvocationPolicy(max_attempts=3))
    assert provider.calls == 1


def test_default_zero_delay_does_not_sleep(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("tests must not sleep")

    monkeypatch.setattr(asyncio, "sleep", forbidden)
    result = invoke(FakeProvider(INFO, (TransientProviderError(), response())), policy=InvocationPolicy(max_attempts=2))
    assert result.attempts == 2


def test_fake_exhaustion_mismatch_and_instance_isolation():
    fake = FakeProvider(INFO, ())
    with pytest.raises(FakeScriptError):
        asyncio.run(fake.generate(request()))
    other = FakeProvider(INFO, (response(),), expected_requests=(request(max_output_tokens=1),))
    with pytest.raises(FakeScriptError):
        asyncio.run(other.generate(request()))
    assert fake.history == other.history == (request(),)
    assert FakeProvider(INFO, ()).history == ()


def test_a_b_a_retry_budget_and_result_state_are_per_call():
    a, b = request(), request(max_output_tokens=1)
    ra = response(usage=TokenUsage(total_tokens=64))
    fake = FakeProvider(INFO, (ra, TransientProviderError(), ProviderError(), ra),
                        expected_requests=(a, b, b, a))
    client = LLMClient(fake, policy=InvocationPolicy(max_total_tokens=64, max_attempts=2))

    async def scenario():
        first = await client.generate(a)
        with pytest.raises(ProviderError):
            await client.generate(b)
        last = await client.generate(a)
        assert first == last and last.attempts == 1

    asyncio.run(scenario())
    assert fake.history == (a, b, b, a)
