"""No external I/O. Exercise the real adapter around scripted SDK-shaped responses."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS

import httpx
from pydantic import SecretStr, ValidationError
import pytest

from quantlab.llm import LLMClient, StructuredOutputError
from quantlab.llm.cloud import CloudBudget, CloudError, CloudSettings, FreeTierVerification
from quantlab.llm.gemini import GeminiProvider, normalize_error
from quantlab.interpretation import build_request, interpret_strategy
from tests.interpretation.helpers import source, ready

SECRET = 'server-only-fixture-key-xxxxxxxx'


def configuration(**changes):
    now = datetime.now(timezone.utc)
    values = dict(enabled=True, model_id='gemini-test', model_allowlist=('gemini-test',),
        api_key=SecretStr(SECRET), verification=FreeTierVerification(model_id='gemini-test',
            checked_at=now - timedelta(minutes=1), expires_at=now + timedelta(hours=1),
            billing_disabled=True, free_inference_eligible=True, structured_text_supported=True))
    values.update(changes)
    return CloudSettings(**values)


def sdk_response(text=None, finish='STOP', **changes):
    value = NS(candidates=[NS(finish_reason=NS(value=finish), content=NS(parts=[NS(
        text=json.dumps(ready()) if text is None else text, thought=False, function_call=None)]))],
        usage_metadata=NS(prompt_token_count=1000, total_token_count=1500))
    for name, change in changes.items():
        setattr(value, name, change)
    return value


class ScriptedSDK:
    def __init__(self, response=None, error=None, count=1000):
        self.models = self
        self.response = response or sdk_response()
        self.error = error
        self.count = count
        self.calls = []

    async def count_tokens(self, **kwargs):
        self.calls.append(('count', kwargs))
        if self.error:
            raise self.error
        return NS(total_tokens=self.count)

    async def generate_content(self, **kwargs):
        self.calls.append(('generate', kwargs))
        return self.response

    @asynccontextmanager
    async def factory(self, settings):
        yield self


def provider(settings=None, sdk=None):
    sdk = sdk or ScriptedSDK()
    return GeminiProvider(settings or configuration(), client_factory=sdk.factory), sdk


def interpret(adapter, value=None):
    return asyncio.run(interpret_strategy(LLMClient(adapter), value or source(), identity=adapter.info.identity))


def test_real_adapter_contract_and_provenance_no_authority():
    adapter, sdk = provider()
    result = interpret(adapter)
    assert result.proposal.approval is None
    assert result.invocation.response.identity == adapter.info.identity
    assert result.invocation.attempts == 1
    assert result.invocation.response.usage.total_tokens == 1500
    assert adapter.budget.snapshot() == (2, False)
    assert [c[0] for c in sdk.calls] == ['count', 'generate']
    config = sdk.calls[1][1]['config']
    assert config['automatic_function_calling'] == {'disable': True}
    assert config['response_json_schema']['additionalProperties'] is False
    assert 'tools' not in config
    assert SECRET not in json.dumps(sdk.calls)
    assert 'research:TEST' not in json.dumps(sdk.calls[0][1].get('config', {}))


@pytest.mark.parametrize('changes,code', [
    ({'enabled':False},'cloud_disabled'), ({'api_key':None},'credentials_missing'),
    ({'model_id':'gemini-paid'},'free_policy_rejected'), ({'verification':None},'free_policy_rejected'),
    ({'model_allowlist':()},'free_policy_rejected'), ({'provider_id':'openrouter'},'unsupported_provider'),
    ({'provider_allowlist':()},'unsupported_provider'),
])
def test_fail_closed_without_wire_requests(changes, code):
    adapter, sdk = provider(configuration(**changes))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == code
    assert not sdk.calls and adapter.budget.used == 0


def test_attestation_expiry_and_billing_are_not_model_name_assumptions():
    config = configuration()
    now = datetime.now(timezone.utc)
    assert config.rejection(now + timedelta(days=1)) == 'free_policy_rejected'
    assert config.rejection(now - timedelta(days=1)) == 'free_policy_rejected'
    with pytest.raises(ValidationError):
        FreeTierVerification(**(config.verification.model_dump() | {'billing_disabled':False}))
    with pytest.raises(ValidationError):
        CloudSettings(free_only=False)


@pytest.mark.parametrize('error,code', [
    (httpx.ConnectError(SECRET),'provider_unavailable'),
    (httpx.ConnectTimeout(SECRET),'provider_timeout'),
    (httpx.ReadTimeout(SECRET),'provider_timeout'),
    (TimeoutError(SECRET),'provider_timeout'),
    (RuntimeError(SECRET),'provider_unavailable'),
])
def test_network_failures_no_retry_and_redaction(error, code):
    adapter, sdk = provider(sdk=ScriptedSDK(error=error))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == code
    assert SECRET not in str(caught.value) and SECRET not in repr(caught.value)
    assert len(sdk.calls) == 1 and adapter.budget.snapshot() == (2, False)


@pytest.mark.parametrize('code,details,expected', [
    (401, [], 'credentials_invalid'), (403, [], 'credentials_invalid'),
    (429, [], 'rate_limited'),
    (429, [{'@type':'type.googleapis.com/google.rpc.QuotaFailure'}], 'quota_exhausted'),
    (503, [], 'provider_unavailable'),
])
def test_http_error_normalization(code, details, expected):
    error = RuntimeError(SECRET)
    error.code, error.details = code, details
    adapter, sdk = provider(sdk=ScriptedSDK(error=error))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == expected
    assert SECRET not in str(caught.value)
    assert len(sdk.calls) == 1


@pytest.mark.parametrize('text', ['not json','{}', '{"status":"READY","approval":true}',
    json.dumps(ready())[:-3], json.dumps(ready()).replace('"status": "READY"','"status": "READY", "status": "READY"')])
def test_strict_invalid_output_not_retried(text):
    adapter, sdk = provider(sdk=ScriptedSDK(response=sdk_response(text)))
    with pytest.raises(StructuredOutputError):
        interpret(adapter)
    assert len(sdk.calls) == 2


@pytest.mark.parametrize('response', [sdk_response(finish='MAX_TOKENS'), sdk_response(SECRET),
    sdk_response('x'*65537), sdk_response(candidates=[]), sdk_response(usage_metadata=None),
    sdk_response(usage_metadata=NS(prompt_token_count=1000,total_token_count=9000))])
def test_incomplete_or_unbounded_response_rejected(response):
    adapter, sdk = provider(sdk=ScriptedSDK(response=response))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == 'invalid_model_response'


def test_budget_counts_failures_no_fallback_and_concurrency():
    adapter, sdk = provider(configuration(max_session_requests=2))
    interpret(adapter)
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == 'budget_exhausted'
    assert len(sdk.calls) == 2
    budget = CloudBudget(configuration())
    budget.acquire()
    with pytest.raises(CloudError) as caught:
        budget.acquire()
    assert caught.value.code == 'cloud_busy' and budget.used == 2
    budget.release()


def test_oversized_and_secret_input_never_transmitted():
    for config, text in [(configuration(max_prompt_bytes=1024),'safe'), (configuration(),SECRET)]:
        adapter, sdk = provider(config)
        with pytest.raises(CloudError) as caught:
            interpret(adapter, source(strategy_text=text))
        assert caught.value.code == 'cloud_input_rejected' and not sdk.calls
    adapter, sdk = provider(sdk=ScriptedSDK(count=32768))
    with pytest.raises(CloudError):
        interpret(adapter)
    assert [c[0] for c in sdk.calls] == ['count']


def test_sparse_decimal_and_excessive_nesting_rejected_before_domain_conversion():
    value=ready()
    value['draft']['long']['entry']['rules'][0]['right']={'kind':'constant','value':'1e9999999'}
    adapter,_=provider(sdk=ScriptedSDK(response=sdk_response(json.dumps(value))))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code=='invalid_model_response'
    adapter,_=provider(sdk=ScriptedSDK(response=sdk_response('['*100+'0'+']'*100)))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code=='invalid_model_response'
