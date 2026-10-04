"""Architecture tests for the bounded Phase 17 MCP layer."""

import ast
import asyncio
from pathlib import Path
import subprocess
import sys

from mcp.server.mcpserver import MCPServer

from quantlab.mcp import build_mcp_server
from quantlab.mcp.server import SERVER_NAME, SERVER_VERSION


PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "quantlab" / "mcp"


def test_public_builder_returns_real_mcp_server():
    server = build_mcp_server()

    assert type(server) is MCPServer
    assert server.name == SERVER_NAME
    assert server.version == SERVER_VERSION


def test_server_is_fresh_per_build():
    first = build_mcp_server()
    second = build_mcp_server()

    assert first is not second


def test_server_exposes_only_the_bounded_validation_surface():
    server = build_mcp_server()

    assert [tool.name for tool in asyncio.run(server.list_tools())] == [
        "validate_strategy_content",
    ]


def test_mcp_package_does_not_import_orchestration():
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue

            assert all(
                not name.startswith("quantlab.orchestration")
                for name in names
            ), f"{path.name} must not expose human-review orchestration through MCP"


def test_mcp_package_contains_no_eval_or_exec_calls():
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec"}
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"eval", "exec"}


def test_only_public_strategy_content_is_imported_from_quantlab():
    # An explicit allowlist prevents new quantitative, storage, orchestration,
    # approval, or private implementation dependencies in this first adapter.
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(("quantlab", "langgraph"))
                           for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                assert not module.startswith("langgraph")
                if module.startswith("quantlab"):
                    assert module == "quantlab.strategies"
                    assert [alias.name for alias in node.names] == ["StrategyContent"]
        classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert not {"RuleEvaluator", "FeatureRegistry", "ResearchGraph", "ApprovalRecord"} & set(classes)


def test_fresh_import_does_not_build_server_or_start_transport():
    code = """
import socket
import sys
from mcp.server.mcpserver import MCPServer
def forbidden(*args, **kwargs):
    raise AssertionError('server startup or network access forbidden')
MCPServer.__init__ = forbidden
MCPServer.run = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
import quantlab.mcp
assert quantlab.mcp.__all__ == ['build_mcp_server']
for module in ('quantlab.orchestration', 'quantlab.data.storage',
               'quantlab.features', 'quantlab.backtesting', 'quantlab.risk',
               'quantlab.ml', 'quantlab.analytics', 'quantlab.validation'):
    assert module not in sys.modules, module
"""
    result = subprocess.run([sys.executable, "-B", "-c", code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_main_starts_only_stdio(monkeypatch):
    from quantlab.mcp import server

    calls = []

    def observe(self, *, transport):
        calls.append(transport)

    monkeypatch.setattr(MCPServer, "run", observe)
    server.main()
    assert calls == ["stdio"]
