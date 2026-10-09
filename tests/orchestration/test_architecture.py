import ast
import asyncio
import builtins
from importlib.metadata import version
import os
from pathlib import Path
import socket
import subprocess
import sys
import tomllib
import urllib.request

import pytest

import quantlab.orchestration as orchestration
from quantlab.data import MarketBar, MarketQuote
from quantlab.strategies import StrategySpecification
from .helpers import decision, setup


def test_dependency_and_explicit_exports():
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import Command, interrupt
    from langgraph.checkpoint.memory import InMemorySaver
    assert all((StateGraph, START, END, Command, interrupt, InMemorySaver))
    assert tuple(map(int, version("langgraph").split(".")[:2])) >= (1, 2)
    assert sys.version_info[:2] >= (3, 11)
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    assert project["dependencies"] == [
        "pydantic>=2.10,<3", "langgraph>=1.2.12,<2", "mcp>=2.3,<3",
        "fastapi>=0.143,<1", "uvicorn>=0.30,<1",
    ]
    assert len(orchestration.__all__) == len(set(orchestration.__all__))
    assert all(hasattr(orchestration, name) for name in orchestration.__all__)


def test_fresh_import_no_network_credentials_vendors_or_quant_engines():
    script = '''
import os, socket, urllib.request, sys
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
import quantlab.orchestration
for name in ('openai', 'anthropic', 'google.genai', 'ollama', 'langchain_openai',
    'langchain_anthropic', 'quantlab.backtesting', 'quantlab.risk', 'quantlab.analytics',
    'quantlab.ml', 'quantlab.features', 'quantlab.validation', 'quantlab.data.storage'):
    assert name not in sys.modules, name
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("multimodal", [False, True])
@pytest.mark.parametrize("action", list(orchestration.ReviewAction))
def test_workflow_no_io_credentials_tracing_or_quant_authority(monkeypatch, multimodal, action):
    import quantlab.backtesting as backtesting
    import quantlab.risk as risk
    import quantlab.analytics as analytics
    import quantlab.ml as ml

    def denied(*args, **kwargs):
        raise AssertionError("forbidden authority or I/O")

    original = os._Environ.__getitem__
    def guarded(self, key):
        if any(part in key.upper() for part in ("API_KEY", "SECRET", "TOKEN", "CREDENTIAL")):
            raise AssertionError("credential lookup forbidden")
        return original(self, key)

    async def scenario():
        graph, request, fake = setup(multimodal=multimodal)
        # Warm dependency imports before denying arbitrary application file access.
        await graph._compiled.aget_state(graph._config("unused"))
        with monkeypatch.context() as patch:
            patch.setattr(os._Environ, "__getitem__", guarded)
            for target, name in ((socket, "socket"), (socket, "create_connection"),
                (urllib.request, "urlopen"), (builtins, "open"), (Path, "open"),
                (MarketBar, "__init__"), (MarketQuote, "__init__"),
                (backtesting, "run_backtest"), (risk, "evaluate_entry_risk"),
                (analytics, "analyze_performance"), (ml, "train_model"),
                (StrategySpecification, "approve"), (StrategySpecification, "mark_validated")):
                patch.setattr(target, name, denied)
            first = await orchestration.start_workflow(graph, request)
            final = await orchestration.resume_workflow(graph, thread_id=request.thread_id,
                                                        decision=decision(first, action))
            assert final.proposal.approval is None and len(fake.history) == 1
    asyncio.run(scenario())


def test_static_no_executable_content_external_io_or_authority():
    forbidden_imports = ("openai", "anthropic", "google", "ollama", "langchain_openai",
        "langchain_anthropic", "langchain.agents", "langsmith", "socket", "http", "urllib",
        "subprocess", "pathlib", "mcp", "fastapi", "quantlab.backtesting", "quantlab.risk",
        "quantlab.analytics", "quantlab.features", "quantlab.ml", "quantlab.validation",
        "quantlab.portfolio", "quantlab.execution", "quantlab.paper", "quantlab.api", "quantlab.ui")
    forbidden_calls = {"eval", "exec", "compile", "open", "read_text", "read_bytes", "urlopen",
        "run_backtest", "evaluate_entry_risk", "analyze_performance", "train_model", "approve",
        "mark_validated", "MarketBar", "MarketQuote", "uuid4", "now", "utcnow", "generate"}
    for path in Path(orchestration.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                         else [name.name for name in node.names])
                assert not any(name.startswith(forbidden_imports) for name in names), path
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                # The one legitimate compile is StateGraph.compile.
                if name == "compile":
                    assert isinstance(node.func, ast.Attribute) and node.func.value.id == "graph"
                else:
                    assert name not in forbidden_calls, (path, name)
            elif isinstance(node, ast.While):
                pytest.fail("orchestration must not contain a free-running loop")


@pytest.mark.parametrize("flag", ["LANGSMITH_TRACING", "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_HANDLER"])
def test_environment_tracing_rejected_before_invocation(monkeypatch, flag):
    async def scenario():
        graph, request, fake = setup()
        with monkeypatch.context() as patch:
            patch.setenv(flag, "true")
            with pytest.raises(orchestration.OrchestrationInputError):
                await orchestration.start_workflow(graph, request)
            assert not fake.history
        # Refusal creates no checkpoint and doesn't consume this workflow ID.
        first = await orchestration.start_workflow(graph, request)
        with monkeypatch.context() as patch:
            patch.setenv(flag, "true")
            with pytest.raises(orchestration.OrchestrationInputError):
                await orchestration.resume_workflow(graph, thread_id=request.thread_id,
                                                     decision=decision(first))
        final = await orchestration.resume_workflow(graph, thread_id=request.thread_id,
                                                    decision=decision(first))
        assert final.result == first.result and len(fake.history) == 1
    asyncio.run(scenario())


def test_inherited_runnable_callbacks_not_invoked():
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.runnables.config import var_child_runnable_config
    class ForbiddenCallback(BaseCallbackHandler):
        raise_error = True
        def on_chain_start(self, *args, **kwargs):
            raise AssertionError("inherited callback")
    async def scenario():
        graph, request, _ = setup()
        token = var_child_runnable_config.set({"callbacks": [ForbiddenCallback()]})
        try:
            first = await orchestration.start_workflow(graph, request)
            await orchestration.resume_workflow(graph, thread_id=request.thread_id,
                                                decision=decision(first))
        finally:
            var_child_runnable_config.reset(token)
    asyncio.run(scenario())
