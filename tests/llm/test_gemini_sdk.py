"""Official SDK serialization and retry behavior via httpx.MockTransport; no network."""
import asyncio
from contextlib import asynccontextmanager
import json

from google import genai
from google.genai import errors, types
import httpx
import pytest

from quantlab.llm.cloud import CloudError
from quantlab.llm.gemini import GeminiProvider
from tests.interpretation.helpers import ready
from .test_gemini import configuration, interpret, SECRET


@pytest.mark.parametrize('status,details,expected',[(401,[],'credentials_invalid'),
    (400,[{'@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'API_KEY_INVALID'}],'credentials_invalid'),
    (429,[],'rate_limited'),(429,[{'@type':'type.googleapis.com/google.rpc.QuotaFailure'}],'quota_exhausted'),
    (503,[],'provider_unavailable')])
def test_official_sdk_errors_are_sanitized_and_not_retried(status,details,expected):
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(status,json={'error':{'code':status,'message':SECRET,
            'status':'RESOURCE_EXHAUSTED','details':details}})
    @asynccontextmanager
    async def factory(settings):
        client=genai.Client(api_key=SECRET,vertexai=False,http_options=types.HttpOptions(
            timeout=settings.timeout_ms,retry_options=types.HttpRetryOptions(attempts=1),
            async_client_args={'transport':httpx.MockTransport(transport)}))
        async with client.aio as aio:
            yield aio
    adapter=GeminiProvider(configuration(),client_factory=factory)
    with pytest.raises(CloudError) as caught:
        interpret(adapter)
    assert caught.value.code==expected
    assert SECRET not in str(caught.value) and len(calls)==1


def test_official_sdk_generate_schema_and_token_accounting():
    calls=[]
    def transport(request):
        calls.append((request.url,json.loads(request.content)))
        if str(request.url).endswith(':countTokens'):
            return httpx.Response(200,json={'totalTokens':1000})
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{
            'role':'model','parts':[{'text':json.dumps(ready())}]}}],
            'usageMetadata':{'promptTokenCount':1000,'candidatesTokenCount':400,
                             'thoughtsTokenCount':100,'totalTokenCount':1500}})
    @asynccontextmanager
    async def factory(settings):
        client=genai.Client(api_key=SECRET,vertexai=False,http_options=types.HttpOptions(
            timeout=settings.timeout_ms,retry_options=types.HttpRetryOptions(attempts=1),
            async_client_args={'transport':httpx.MockTransport(transport)}))
        async with client.aio as aio:
            yield aio
    result=interpret(GeminiProvider(configuration(),client_factory=factory))
    assert result.proposal.approval is None and result.invocation.response.usage.output_tokens==500
    assert len(calls)==2
    assert str(calls[1][0]).endswith('/models/gemini-test:generateContent')
    wire=calls[1][1]
    assert wire['generationConfig']['responseMimeType']=='application/json'
    assert 'responseJsonSchema' in wire['generationConfig']
    assert 'systemInstruction' in wire and 'tools' not in wire
    assert SECRET not in json.dumps(wire)


def test_smoke_has_no_empty_system_count_message():
    from quantlab.llm.smoke import run
    from quantlab.llm.gemini import sdk_client
    from unittest.mock import patch
    calls=[]
    def transport(request):
        wire=json.loads(request.content)
        calls.append(wire)
        if str(request.url).endswith(':countTokens'):
            assert all(p.get('text') for c in wire['contents'] for p in c['parts'])
            return httpx.Response(200,json={'totalTokens':50})
        return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'role':'model',
            'parts':[{'text':'{"schema_version":1,"acknowledgement":"non-sensitive research smoke"}'}]}}],
            'usageMetadata':{'promptTokenCount':50,'totalTokenCount':70}})
    original=genai.Client
    def client(**kwargs):
        kwargs['http_options'].httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        return original(**kwargs)
    with patch('google.genai.Client',client):
        result=asyncio.run(run(configuration()))
    assert result['status']=='PASS' and len(calls)==2
