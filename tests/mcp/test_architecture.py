"""Architecture tests for the bounded Phase 17 MCP layer."""

import ast
import asyncio
from pathlib import Path
import subprocess
import sys

from mcp.server.mcpserver import MCPServer

from quantlab.mcp import build_mcp_server
from quantlab.mcp.server import SERVER_NAME, SERVER_VERSION
from .helpers import TOOL_NAMES


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


def test_server_exposes_only_the_bounded_tool_surface():
    server = build_mcp_server()

    assert [tool.name for tool in asyncio.run(server.list_tools())] == TOOL_NAMES


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


def test_only_permitted_public_services_and_contracts_are_imported():
    allowed = {
        "quantlab": {"analytics", "backtesting", "data", "features", "risk"},
        "quantlab.strategies": {"StrategyContent", "StrategySpecification"},
        "quantlab.data": {"DataQualityReport", "Instrument", "MarketBar", "MarketQuote",
                          "ResampleRequest", "ValidationOptions"},
        "quantlab.features": {"FeatureObservation", "FeatureRequest"},
        "quantlab.risk": {"RiskConfig", "RiskContext", "RiskDecision"},
        "quantlab.analytics": {"AnalyticsConfig", "PerformanceReport"},
        "quantlab.backtesting": {"BacktestConfig", "BacktestResult"},
    }
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
                    assert module in allowed
                    assert {alias.name for alias in node.names} <= allowed[module]
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
               'quantlab.ml', 'quantlab.validation', 'quantlab.paper',
               'quantlab.account', 'quantlab.portfolio'):
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


def test_no_network_filesystem_execution_approval_or_unbounded_operations():
    forbidden_imports = {"socket", "subprocess", "os", "pathlib", "sqlite3", "requests",
                         "httpx", "urllib", "http", "fastapi", "starlette", "shutil"}
    forbidden_calls = {"eval", "exec", "open", "compile", "__import__", "ApprovalRecord",
                       "approve", "mark_validated", "revise", "resume", "RuleEvaluator",
                       "FeatureRegistry", "StrategySpecification", "Fill", "ClosedTrade",
                       "EquityPoint", "BacktestResult"}
    backtest_calls = []
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split('.')[0] for a in node.names} & forbidden_imports
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split('.')[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name) else
                        node.func.attr if isinstance(node.func, ast.Attribute) else "")
                assert name not in forbidden_calls
                if name == "compute_features":
                    assert "registry" not in {kw.arg for kw in node.keywords}
                if name == "run_backtest":
                    # Permit only the one public-service call in the new adapter.
                    assert path.name == "tools.py"
                    assert isinstance(node.func, ast.Attribute)
                    assert isinstance(node.func.value, ast.Name)
                    assert node.func.value.id == "backtesting"
                    adapter = next(n for n in tree.body
                                   if isinstance(n, ast.FunctionDef) and n.name == "run_backtest")
                    assert node in list(ast.walk(adapter))
                    assert len(node.args) == 3
                    assert {kw.arg for kw in node.keywords} == {"instrument", "config"}
                    backtest_calls.append(node)
    assert len(backtest_calls) == 1
