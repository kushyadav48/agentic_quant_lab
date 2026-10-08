"""Authority and side-effect isolation of the offline Python boundary."""
import ast
import builtins
from pathlib import Path
import socket
import subprocess
import sys
import threading
import urllib.request

from quantlab.paper import FillRecord, PaperOrderKernel
from .helpers import config, market, submission


def test_import_performs_no_network_worker_or_transport_start():
    code = """
import socket, threading, sys, urllib.request
def denied(*args, **kwargs):
    raise AssertionError("external execution forbidden")
socket.socket = denied
socket.create_connection = denied
urllib.request.urlopen = denied
threading.Thread.start = denied
import quantlab.paper
for name in ("quantlab.mcp", "quantlab.orchestration", "quantlab.llm",
             "quantlab.interpretation", "quantlab.data.providers.dukascopy",
             "quantlab.data.storage", "fastapi"):
    assert name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-B", "-c", code],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_processing_uses_no_files_network_workers_or_background_tasks(monkeypatch):
    import asyncio
    def denied(*args, **kwargs):
        raise AssertionError("I/O or background execution forbidden")
    kernel = PaperOrderKernel(config())
    quote, command, next_quote = market(), submission(), market(3)
    with monkeypatch.context() as patch:
        for owner, name in [(socket, "socket"), (socket, "create_connection"),
                (urllib.request, "urlopen"), (threading.Thread, "start"),
                (asyncio, "create_task"), (builtins, "open"), (Path, "open"),
                (subprocess, "Popen")]:
            patch.setattr(owner, name, denied)
        kernel.process(quote)
        kernel.process(command)
        records = kernel.process(next_quote)
    assert len([r for r in records if isinstance(r, FillRecord)]) == 1


def test_core_has_no_agent_transport_broker_clock_or_execution_callback_dependency():
    package = Path(__file__).parents[2] / "src" / "quantlab" / "paper"
    forbidden = ("quantlab.mcp", "quantlab.orchestration", "quantlab.llm",
        "quantlab.interpretation", "quantlab.data.providers", "quantlab.data.storage",
        "fastapi", "langgraph", "mcp", "socket", "urllib", "http", "subprocess",
        "threading", "asyncio", "random", "uuid", "sqlite3")
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names])
                assert not any(name.startswith(forbidden) for name in modules), source
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                assert name not in {"eval", "exec", "now", "utcnow", "uuid4", "sleep", "approve",
                    "mark_validated", "run_backtest", "create_task"}, (source, name)


def test_public_exports_are_explicit_and_existing_engines_do_not_import_paper():
    import quantlab.paper as paper
    assert len(paper.__all__) == len(set(paper.__all__))
    assert all(hasattr(paper, name) for name in paper.__all__)
    package = Path(__file__).parents[2] / "src" / "quantlab"
    for directory in ("backtesting", "risk", "features", "analytics", "validation", "orchestration", "mcp"):
        for source in (package / directory).glob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("quantlab.paper")
                elif isinstance(node, ast.Import):
                    assert not any(alias.name.startswith("quantlab.paper") for alias in node.names)
