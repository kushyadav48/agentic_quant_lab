"""Focused acceptance regressions. All provider I/O uses deterministic doubles."""
import json
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from decimal import Decimal

import pytest

from quantlab.llm.cloud import CloudError
from tests.interpretation.helpers import ready
from tests.llm.test_gemini import SECRET, ScriptedSDK, provider, interpret, sdk_response


def test_sdk_transport_cannot_switch_to_implicit_aiohttp_retries(monkeypatch):
    from google import genai
    from google.genai import _api_client
    from quantlab.llm.gemini import sdk_client
    from tests.llm.test_gemini import configuration
    captured = {}
    @asynccontextmanager
    async def aio():
        yield object()
    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.aio = aio()
        def close(self):
            pass
    monkeypatch.setattr(genai, 'Client', Client)
    async def inspect():
        async with sdk_client(configuration()):
            pass
    asyncio.run(inspect())
    # Real SDK transport-selection rule, simulating optional aiohttp availability.
    monkeypatch.setattr(_api_client, 'has_aiohttp', True)
    api = SimpleNamespace(_http_options=captured['http_options'])
    assert not _api_client.BaseApiClient._use_aiohttp(api)


def test_json_escaped_provider_secret_cannot_enter_retained_interpretation():
    value = ready()
    value['draft']['description'] = SECRET
    text = json.dumps(value).replace(SECRET, ''.join('\\u%04x' % ord(c) for c in SECRET))
    assert SECRET not in text
    adapter, _ = provider(sdk=ScriptedSDK(response=sdk_response(text)))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == 'invalid_model_response'


def test_float_decimal_output_does_not_silently_round_strategy_threshold():
    value = ready()
    exact = '1.12345678901234567890123456789'
    value['draft']['long']['entry']['rules'][0]['right'] = {'kind':'constant','value':'decimal-placeholder'}
    text = json.dumps(value).replace('"decimal-placeholder"', exact)
    adapter, _ = provider(sdk=ScriptedSDK(response=sdk_response(text)))
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code == 'invalid_model_response'


def test_decimal_string_threshold_remains_exact():
    value = ready()
    exact = '1.12345678901234567890123456789'
    value['draft']['long']['entry']['rules'][0]['right'] = {'kind':'constant','value':exact}
    adapter, _ = provider(sdk=ScriptedSDK(response=sdk_response(json.dumps(value))))
    result = interpret(adapter)
    assert result.proposal.content.long.entry.rules[0].right.value == Decimal(exact)


def test_escaped_secret_failure_leaves_no_api_artifact_or_log(caplog):
    from tests.api.test_ai import connection, run, AI
    from tests.api.helpers import READ
    value = ready()
    value['draft']['description'] = SECRET
    text = json.dumps(value).replace(SECRET, ''.join('\\u%04x' % ord(c) for c in SECRET))
    with connection(sdk=ScriptedSDK(response=sdk_response(text))) as (client, jobs, sdk, owners):
        job_id, done = run(client)
        assert done['state'] == 'failed' and done['failure'] == 'invalid_model_response'
        response = client.get(AI + '/' + job_id + '/result', headers=READ)
        assert response.status_code == 409 and not jobs.results
        assert SECRET not in json.dumps(done) + response.text + caplog.text
