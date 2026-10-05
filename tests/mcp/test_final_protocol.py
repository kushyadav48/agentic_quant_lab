"""Complete allowlist through real SDK clients and actual local stdio."""
import asyncio
from copy import deepcopy
import json
import socket
import sys

import anyio
from jsonschema import validate
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
import pytest

from quantlab.mcp import build_mcp_server, tools
from .helpers import TOOL_NAMES, valid_backtest_request, valid_strategy_content
from .research_helpers import requests
from .test_queries import inputs


async def exercise(client, raw):
    listed = await client.list_tools()
    assert [tool.name for tool in listed.tools] == TOOL_NAMES
    schemas = {tool.name: tool for tool in listed.tools}
    for name in TOOL_NAMES[:14]:
        key = "content" if name == "validate_strategy_content" else "request"
        request = valid_strategy_content() if key == "content" else raw[name]
        expected = getattr(tools, name)(request)
        validate({key: request}, schemas[name].input_schema)
        result = await client.call_tool(name, {key: request})
        assert not result.is_error
        assert result.structured_content == expected.model_dump(mode="json")
        validate(result.structured_content, schemas[name].output_schema)
    async def call(name, request):
        validate({"request": request}, schemas[name].input_schema)
        result = await client.call_tool(name, {"request": request})
        assert not result.is_error
        validate(result.structured_content, schemas[name].output_schema)
        return result.structured_content
    submission = {"idempotency_key": "protocol_run", "operation":
                  valid_backtest_request() | {"kind": "backtest"}}
    queued = await call("submit_research_operation", submission)
    assert queued["success"] and queued["value"]["state"] == "queued"
    reference = {"operation_id": queued["value"]["operation_id"]}
    completed = await call("execute_research_operation", reference)
    assert completed["success"] and completed["value"]["state"] == "completed"
    assert json.loads(completed["value"]["result_json"])["success"] is True
    assert await call("get_research_operation", reference) == completed
    assert await call("execute_research_operation", reference) == completed
    assert await call("submit_research_operation", submission) == completed
    cancelled_input = deepcopy(submission)
    cancelled_input["idempotency_key"] = "protocol_cancel"
    cancel = await call("submit_research_operation", cancelled_input)
    cancel_ref = {"operation_id": cancel["value"]["operation_id"]}
    cancelled = await call("cancel_research_operation", cancel_ref)
    assert cancelled["success"] and cancelled["value"]["state"] == "cancelled"
    assert not (await call("execute_research_operation", cancel_ref))["success"]
    assert not (await call("cancel_research_operation", reference))["success"]
    conflict = deepcopy(submission)
    conflict["operation"]["config"]["quantity"] = "3"
    assert (await call("submit_research_operation", conflict))["issues"][0]["code"] == "idempotency_conflict"
    for name in TOOL_NAMES[7:]:
        arguments = {"request": {"secret": "C:/private/file.py"}}
        result = await client.call_tool(name, arguments)
        assert not result.is_error and not result.structured_content["success"]
        assert "secret" not in result.model_dump_json() and "private" not in result.model_dump_json()
        result = await client.call_tool(name, {"request": "{}"})
        assert not result.is_error and not result.structured_content["success"]
        result = await client.call_tool(name, {"request": {}, "secret": "private"})
        assert result.is_error and "Invalid tool arguments." in result.content[0].text
        assert "secret" not in result.model_dump_json()
    assert not (await client.list_resources()).resources
    assert not (await client.list_prompts()).prompts


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("stdio", [False, True])
def test_complete_protocol_surface(mode, stdio, inputs, monkeypatch):
    raw = inputs | requests()
    before = deepcopy(raw)
    def denied(*args, **kwargs):
        raise AssertionError("network authority forbidden")
    async def scenario():
        target = (StdioServerParameters(command=sys.executable, args=["-m", "quantlab.mcp.server"])
                  if stdio else build_mcp_server())
        with monkeypatch.context() as patch:
            for name in ("connect", "connect_ex", "bind", "listen"):
                patch.setattr(socket.socket, name, denied)
            patch.setattr(socket, "create_connection", denied)
            with anyio.fail_after(60):
                async with Client(target, mode=mode) as client:
                    await exercise(client, raw)
    asyncio.run(scenario())
    assert raw == before
