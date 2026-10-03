import ast
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

from pydantic import ValidationError
import pytest

import quantlab.llm as llm
from .helpers import Answer, INFO, request, response


def test_offline_structured_pipeline_repeatable(monkeypatch):
    async def scenario():
        def denied(*args, **kwargs):
            raise AssertionError("network forbidden")

        # Patch after event-loop creation: asyncio itself may create a socketpair.
        monkeypatch.setattr(socket, "socket", denied)
        monkeypatch.setattr(socket, "create_connection", denied)
        monkeypatch.setattr(urllib.request, "urlopen", denied)
        req = request(structured_output=llm.structured_output(Answer))
        raw = response(text='{"count":3,"label":"offline"}',
                       usage=llm.TokenUsage(input_tokens=5, output_tokens=8, total_tokens=13))
        fake = llm.FakeProvider(INFO, (llm.RateLimitError(), raw, raw), expected_requests=(req, req, req))
        client = llm.LLMClient(fake, policy=llm.InvocationPolicy(max_attempts=2, max_total_tokens=64))
        first, result = await llm.generate_structured(client, req, Answer)
        second, replay = await llm.generate_structured(client, req, Answer)
        assert first == second == Answer(count=3, label="offline")
        assert result.attempts == 2 and replay.attempts == 1
        assert llm.InvocationResult.model_validate_json(result.model_dump_json()) == result
        assert req == request(structured_output=llm.structured_output(Answer))
        assert fake.history == (req, req, req)

    asyncio.run(scenario())


def test_fresh_import_no_network_or_credential_reads():
    script = """
import os, socket, urllib.request
def denied(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket = denied
socket.create_connection = denied
urllib.request.urlopen = denied
original = os._Environ.__getitem__
def guarded(self, key):
    if any(part in key.upper() for part in ('API_KEY', 'SECRET', 'TOKEN', 'CREDENTIAL')):
        raise AssertionError('credential lookup forbidden')
    return original(self, key)
os._Environ.__getitem__ = guarded
import quantlab.llm
import sys
assert not any(name in sys.modules for name in ('openai', 'anthropic', 'quantlab.backtesting',
    'quantlab.risk', 'quantlab.analytics', 'quantlab.strategies', 'quantlab.ml'))
"""
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_package_has_no_quant_authority_or_executable_content():
    directory = Path(llm.__file__).parent
    for path in directory.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("quantlab.")
            elif isinstance(node, ast.Import):
                assert all(not name.name.startswith(("quantlab.", "openai", "anthropic")) for name in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec", "run_backtest", "evaluate_risk")


def test_credentials_have_no_public_contract_field_or_environment_dependency(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-secret")
    artifacts = (request(), response(), INFO, llm.InvocationPolicy())
    for artifact in artifacts:
        assert "sentinel-secret" not in repr(artifact) + str(artifact) + artifact.model_dump_json()
        assert "api_key" not in artifact.model_dump()
        with pytest.raises(ValidationError) as caught:
            type(artifact).model_validate(artifact.model_dump() | {"api_key": "sentinel-secret"})
        assert "sentinel-secret" not in str(caught.value) + repr(caught.value)
    fake = llm.FakeProvider(INFO, (response(),))
    result = asyncio.run(llm.LLMClient(fake).generate(request()))
    assert "sentinel-secret" not in result.model_dump_json() + repr(fake)


def test_exports_are_explicit_and_resolve():
    assert len(llm.__all__) == len(set(llm.__all__))
    assert all(hasattr(llm, name) for name in llm.__all__)
