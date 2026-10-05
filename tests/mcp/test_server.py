"""Server-level MCP protocol tests."""

import asyncio
from copy import deepcopy
import json
import socket

from jsonschema import Draft202012Validator, validate
import pytest
from mcp.server.mcpserver.exceptions import UnexpectedToolError

from quantlab.mcp import build_mcp_server
from quantlab.mcp.models import StrategyValidationResult
from quantlab.mcp.tools import validate_strategy_content

from .helpers import TOOL_NAMES, valid_strategy_content


def test_server_registers_exact_checkpoint_two_allowlist():
    server = build_mcp_server()

    tools = asyncio.run(server.list_tools())

    assert [tool.name for tool in tools] == TOOL_NAMES


def test_strategy_validation_tool_has_structured_output_schema():
    server = build_mcp_server()

    tools = asyncio.run(server.list_tools())

    tool = tools[0]

    assert tool.name == "validate_strategy_content"
    assert tool.output_schema is not None
    assert tool.output_schema["type"] == "object"
    assert set(tool.output_schema["properties"]) == {"valid", "content_digest", "issues"}
    assert tool.output_schema["additionalProperties"] is False
    Draft202012Validator.check_schema(tool.output_schema)


def test_input_schema_requires_raw_content_object_without_prevalidating_domain():
    tool = asyncio.run(build_mcp_server().list_tools())[0]
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert schema["required"] == ["content"]
    assert set(schema["properties"]) == {"content"}
    content_schema = schema["properties"]["content"]
    assert content_schema["type"] == "object"
    assert content_schema["additionalProperties"] is True
    assert "StrategyContent" in content_schema["description"]
    Draft202012Validator.check_schema(schema)
    # Invalid domain fields must reach the adapter for structured issues, not
    # fail inside the SDK's argument model before the tool body can run.
    validate({"content": {"direction": "wrong"}}, schema)


@pytest.mark.parametrize("case", ["valid", "missing", "semantic", "extra", "enum", "tag"])
def test_in_process_sdk_invocation_returns_structured_results_without_network(case, monkeypatch):
    content = valid_strategy_content()
    if case == "missing":
        content.pop("timeframe")
    elif case == "semantic":
        content["direction"] = "short"
    elif case == "extra":
        content["approval"] = "secret-approval"
    elif case == "enum":
        content["direction"] = "secret-enum"
    elif case == "tag":
        content["long"]["entry"]["rules"][0]["left"]["kind"] = "secret-tag"
    before = deepcopy(content)
    expected = validate_strategy_content(content)

    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    async def scenario():
        # Keep socket.socket a class: Windows ProactorEventLoop uses it in
        # isinstance checks for its own wakeup pipe. Deny outgoing connections
        # without interfering with asyncio's local event-loop handles.
        with monkeypatch.context() as patch:
            patch.setattr(socket.socket, "connect", forbidden)
            patch.setattr(socket.socket, "connect_ex", forbidden)
            patch.setattr(socket.socket, "bind", forbidden)
            patch.setattr(socket.socket, "listen", forbidden)
            patch.setattr(socket, "create_connection", forbidden)
            server = build_mcp_server()
            tool = (await server.list_tools())[0]
            result = await server.call_tool("validate_strategy_content", {"content": content})
            assert result.is_error is False
            assert result.structured_content == expected.model_dump(mode="json")
            validate(result.structured_content, tool.output_schema)
            assert StrategyValidationResult.model_validate_json(json.dumps(result.structured_content)) == expected
            assert "secret" not in json.dumps(result.structured_content)
            assert len(result.content) == 1
            assert json.loads(result.content[0].text) == result.structured_content

    asyncio.run(scenario())
    assert expected.valid is (case == "valid")
    assert content == before


def test_sdk_sanitizes_unexpected_internal_failure(monkeypatch):
    from quantlab.strategies import validation

    def fail(content):
        raise RuntimeError("secret internal failure C:/private/module.py")

    monkeypatch.setattr(validation, "validate_content", fail)
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool(
            "validate_strategy_content", {"content": valid_strategy_content()},
        ))
    assert str(caught.value) == "Error executing tool validate_strategy_content"
