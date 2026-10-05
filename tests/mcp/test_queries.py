"""Checkpoint 2: real JSON, canonical services, SDK schemas and trust boundaries."""

import asyncio
from copy import deepcopy
from decimal import Decimal
import json
import socket
import sys

import anyio
from jsonschema import Draft202012Validator, ValidationError as SchemaValidationError, validate
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError
import pytest

from quantlab import analytics, backtesting, data, features, risk
from quantlab.backtesting import BacktestResult
from quantlab.mcp import build_mcp_server
from quantlab.mcp import tools
from quantlab.mcp.models import (
    AdapterIssue, EntryRiskRequest, EntryRiskResult,
    FeatureComputationRequest, FeatureComputationResult,
    MarketDataResampleRequest, MarketDataResampleResult,
    MarketDataValidationRequest, MarketDataValidationResult,
    PerformanceAnalysisRequest, PerformanceAnalysisResult,
)
from tests.analytics.helpers import four_trades
from tests.features.helpers import INSTRUMENT, bars
from tests.risk.test_models import context
from .helpers import (
    ORIGINAL_TOOL_NAMES as TOOL_NAMES, TOOL_NAMES as FINAL_TOOL_NAMES,
    valid_backtest_request, valid_strategy_content,
)


# The original five queries retain their prohibition on backtest execution.
QUERIES = [
    "validate_market_data", "resample_market_data", "compute_features",
    "evaluate_entry_risk", "analyze_performance",
]
CONTRACTS = dict(zip(QUERIES, (
    MarketDataValidationRequest, MarketDataResampleRequest, FeatureComputationRequest,
    EntryRiskRequest, PerformanceAnalysisRequest,
)))
RESULTS = dict(zip(QUERIES, (
    MarketDataValidationResult, MarketDataResampleResult, FeatureComputationResult,
    EntryRiskResult, PerformanceAnalysisResult,
)))
SERVICES = {
    "validate_market_data": (data, "validate_dataset"),
    "resample_market_data": (data, "resample"),
    "compute_features": (features, "compute_features"),
    "evaluate_entry_risk": (risk, "evaluate_entry_risk"),
    "analyze_performance": (analytics, "analyze_performance"),
    "run_backtest": (backtesting, "run_backtest"),
}


@pytest.fixture
def inputs():
    series = [bar.model_dump(mode="json") for bar in bars()]
    instrument = INSTRUMENT.model_dump(mode="json")
    return {
        "validate_market_data": {"observations": deepcopy(series), "instrument": instrument},
        "resample_market_data": {
            "observations": deepcopy(series),
            "request": {"instrument": instrument, "timeframe": "5m", "price_type": "bid",
                        "start_time": series[0]["start_time"], "end_time": series[-1]["end_time"],
                        "missing_policy": "reject"},
        },
        "compute_features": {"bars": series, "instrument": instrument,
                             "requested_features": [{"feature_id": "sma",
                                  "parameters": [{"name": "period", "value": 2}]}]},
        "evaluate_entry_risk": {"context": context().model_dump(mode="json"),
                                "config": {"max_position_quantity": "1"}},
        "analyze_performance": {"result": four_trades().model_dump(mode="json"),
                                "config": {"annualization_factor": "252"}},
        "run_backtest": valid_backtest_request(),
    }


def domain_result(name, request):
    decoded = CONTRACTS[name].model_validate_json(json.dumps(request), strict=True)
    if name == "validate_market_data":
        return data.validate_dataset(decoded.observations, instrument=decoded.instrument,
            options=decoded.options, expected_starts=decoded.expected_starts)
    if name == "resample_market_data":
        return data.resample(decoded.observations, decoded.request)
    if name == "compute_features":
        return features.compute_features(decoded.bars, decoded.requested_features,
                                         instrument=decoded.instrument)
    if name == "evaluate_entry_risk":
        return risk.evaluate_entry_risk(decoded.context, decoded.config)
    return analytics.analyze_performance(decoded.result, decoded.config)


@pytest.mark.parametrize("name", QUERIES)
def test_external_json_reuses_public_service_with_canonical_inputs(name, inputs, monkeypatch):
    request = inputs[name]
    before = json.dumps(request, separators=(",", ":"))
    expected = domain_result(name, request)
    module, service = SERVICES[name]
    original = getattr(module, service)
    calls = []

    def observe(*args, **kwargs):
        calls.append((args, kwargs))
        if name in ("validate_market_data", "resample_market_data"):
            assert type(args[0]) is tuple
            assert all(type(item) is data.MarketBar for item in args[0])
        if name == "resample_market_data":
            assert type(args[1]) is data.ResampleRequest
        elif name == "validate_market_data":
            assert type(kwargs["instrument"]) is data.Instrument
        elif name == "compute_features":
            assert all(type(item) is data.MarketBar for item in args[0])
            assert all(type(item) is features.FeatureRequest for item in args[1])
            assert type(args[1][0].parameters[0].value) is int
            assert set(kwargs) == {"instrument"}  # default trusted registry
        elif name == "evaluate_entry_risk":
            assert type(args[0]) is risk.RiskContext and type(args[1]) is risk.RiskConfig
            assert type(args[0].requested_quantity) is Decimal
            assert args[0].side is risk.RiskSide.LONG
        elif name == "analyze_performance":
            assert type(args[0]) is BacktestResult and type(args[1]) is analytics.AnalyticsConfig
            assert type(args[0].closed_trades) is tuple
        return original(*args, **kwargs)

    monkeypatch.setattr(module, service, observe)
    result = getattr(tools, name)(request)
    assert result.success and not result.issues
    assert result.value == expected
    assert len(calls) == 1
    assert json.dumps(request, separators=(",", ":")) == before
    assert result == getattr(tools, name)(dict(reversed(list(request.items()))))


BAD = [None, [], "{}", {1: "secret"}, {"secret": (1,)}, {"secret": b"secret"},
       {"secret": Decimal("1")}, {"secret": object()}, {"secret": float("nan")},
       {"secret": float("inf")}, {"secret": float("-inf")}]


@pytest.mark.parametrize("name", QUERIES)
@pytest.mark.parametrize("bad", BAD)
def test_non_json_wire_values_fail_safely_in_adapter_and_sdk(name, bad):
    expected = getattr(tools, name)(bad)
    assert not expected.success and expected.value is None
    assert [i.code for i in expected.issues] == ["invalid_json_request"]
    assert "secret" not in expected.model_dump_json()
    result = asyncio.run(build_mcp_server().call_tool(name, {"request": bad}))
    assert not result.is_error
    assert result.structured_content == expected.model_dump(mode="json")


@pytest.mark.parametrize("name", QUERIES)
def test_cycles_fail_safely_in_adapter_and_sdk(name):
    request = {}
    request["secret-cycle"] = request
    assert not getattr(tools, name)(request).success
    result = asyncio.run(build_mcp_server().call_tool(name, {"request": request}))
    assert result.structured_content["issues"][0]["code"] == "invalid_json_request"
    assert "secret" not in json.dumps(result.structured_content)


def malformed_domain(name, request):
    if name == "validate_market_data":
        request["observations"][0]["high"] = "0.1"
    elif name == "resample_market_data":
        request["request"]["timeframe"] = "1d"
    elif name == "compute_features":
        request["requested_features"][0]["parameters"][0]["value"] = True
    elif name == "evaluate_entry_risk":
        request["context"]["side"] = "exit_long"
    else:
        request["result"]["closed_trades"][0]["net_pnl"] = "999"


@pytest.mark.parametrize("name", QUERIES)
def test_existing_domain_rejects_invalid_input_without_repair(name, inputs, monkeypatch):
    request = inputs[name]
    malformed_domain(name, request)
    before = deepcopy(request)
    with pytest.raises(ValidationError):
        CONTRACTS[name].model_validate_json(json.dumps(request), strict=True)
    module, service = SERVICES[name]

    def forbidden(*args, **kwargs):
        raise AssertionError("malformed contract must not reach service")

    monkeypatch.setattr(module, service, forbidden)
    result = getattr(tools, name)(request)
    assert not result.success and result.issues and result.value is None
    assert request == before


@pytest.mark.parametrize("name", QUERIES)
def test_unknown_fields_and_values_are_sanitized_deterministically(name, inputs):
    request = inputs[name]
    request["secret-field"] = "secret-value C:/private/file.py"
    request["another-secret"] = "secret-password"
    before = deepcopy(request)
    result = getattr(tools, name)(request)
    assert not result.success
    assert all(i.location == ("<extra>",) for i in result.issues)
    assert "secret" not in result.model_dump_json()
    assert result == getattr(tools, name)(dict(reversed(list(request.items()))))
    assert request == before


@pytest.mark.parametrize("name", QUERIES)
@pytest.mark.parametrize("case", ["valid", "domain", "wire", "extra"])
def test_actual_sdk_output_matches_schema_without_network(name, case, inputs, monkeypatch):
    request = inputs[name]
    if case == "domain":
        malformed_domain(name, request)
    elif case == "wire":
        request["secret"] = (1, 2)
    elif case == "extra":
        request["secret-extra"] = "secret-value"
    before = deepcopy(request)
    expected = getattr(tools, name)(request)

    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    async def scenario():
        with monkeypatch.context() as patch:
            for operation in ("connect", "connect_ex", "bind", "listen"):
                patch.setattr(socket.socket, operation, forbidden)
            patch.setattr(socket, "create_connection", forbidden)
            server = build_mcp_server()
            tool = next(t for t in await server.list_tools() if t.name == name)
            Draft202012Validator.check_schema(tool.input_schema)
            Draft202012Validator.check_schema(tool.output_schema)
            if case == "valid":
                validate({"request": request}, tool.input_schema)
            result = await server.call_tool(name, {"request": request})
            assert not result.is_error
            assert result.structured_content == expected.model_dump(mode="json")
            validate(result.structured_content, tool.output_schema)
            assert RESULTS[name].model_validate_json(json.dumps(result.structured_content)) == expected
            assert json.loads(result.content[0].text) == result.structured_content
            assert "secret" not in json.dumps(result.structured_content)

    asyncio.run(scenario())
    assert request == before


@pytest.mark.parametrize("name", QUERIES)
def test_core_python_contracts_remain_strict(name, inputs):
    with pytest.raises(ValidationError):
        CONTRACTS[name].model_validate(inputs[name])
    canonical = CONTRACTS[name].model_validate_json(json.dumps(inputs[name]), strict=True)
    assert CONTRACTS[name].model_validate(canonical) == canonical


@pytest.mark.parametrize("name", QUERIES[1:])
def test_expected_service_rejections_are_sanitized(name, inputs, monkeypatch):
    module, service = SERVICES[name]
    error = (risk.RiskInputError if name == "evaluate_entry_risk" else
             analytics.AnalyticsInputError if name == "analyze_performance" else ValueError)
    def reject(*args, **kwargs):
        raise error("secret detail C:/private/file.py")

    monkeypatch.setattr(module, service, reject)
    result = getattr(tools, name)(inputs[name])
    assert not result.success
    assert [i.code for i in result.issues] == ["service_input_error"]
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("name", QUERIES)
@pytest.mark.parametrize("error", [RuntimeError, TypeError])
def test_unexpected_failures_remain_sanitized_sdk_failures(name, error, inputs, monkeypatch):
    module, service = SERVICES[name]

    def fail(*args, **kwargs):
        raise error("secret internal detail C:/private/file.py")

    monkeypatch.setattr(module, service, fail)
    with pytest.raises(error):
        getattr(tools, name)(inputs[name])
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool(name, {"request": inputs[name]}))
    assert str(caught.value) == f"Error executing tool {name}"


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_sdk_missing_extra_and_stringified_arguments_do_not_leak_or_coerce(name, inputs):
    key = "content" if name == "validate_strategy_content" else "request"
    request = valid_strategy_content() if key == "content" else inputs[name]
    for arguments in ({}, {key: request, "secret-key": "secret-value"}):
        with pytest.raises(ToolError) as caught:
            asyncio.run(build_mcp_server().call_tool(name, arguments))
        assert type(caught.value) is ToolError
        assert "Invalid tool arguments." in str(caught.value)
        assert "secret" not in str(caught.value)
    result = asyncio.run(build_mcp_server().call_tool(name, {key: json.dumps(request)}))
    assert result.structured_content.get("success", result.structured_content.get("valid")) is False


def test_quality_reports_preserve_order_duplicates_and_schedule(inputs):
    request = inputs["validate_market_data"]
    request["observations"] = [request["observations"][1], request["observations"][0],
                               request["observations"][0]]
    request["options"] = {"max_gap": "PT1M", "homogeneous_dataset": True}
    request["expected_starts"] = ["2026-01-01T00:00:00Z", "2026-01-01T00:02:00Z"]
    before = deepcopy(request)
    result = tools.validate_market_data(request)
    assert result.success and not result.value.valid
    assert result.value == domain_result("validate_market_data", request)
    assert result.value.observation_count == 3 and result.value.duplicate_count == 1
    assert result.value.out_of_order_count == 1 and result.value.gaps_checked
    assert request == before


def test_quotes_decode_without_inventing_provenance(inputs):
    quote = {"instrument_id": INSTRUMENT.instrument_id, "source_id": "fixture",
             "dataset_id": "raw", "timestamp": "2026-01-01T00:00:00Z",
             "available_at": "2026-01-01T00:00:01Z", "bid": "1", "ask": "2"}
    request = inputs["validate_market_data"]
    request["observations"] = [quote]
    assert tools.validate_market_data(request).value == domain_result("validate_market_data", request)
    resampling = inputs["resample_market_data"]
    resampling["observations"] = [quote]
    value = tools.resample_market_data(resampling).value
    assert value == domain_result("resample_market_data", resampling)
    assert value[0].source_id == "fixture" and value[0].volume is None
    del quote["source_id"]
    assert not tools.validate_market_data(request).success
    assert not tools.resample_market_data(resampling).success


@pytest.mark.parametrize("case", ["order", "duplicates", "mixed_source", "upsampling", "missing"])
def test_resampling_rejects_unsafe_semantics(inputs, case):
    request = inputs["resample_market_data"]
    if case == "order":
        request["observations"].reverse()
    elif case == "duplicates":
        request["observations"].insert(0, deepcopy(request["observations"][0]))
    elif case == "mixed_source":
        request["observations"][1]["source_id"] = "secret-source"
    elif case == "missing":
        request["observations"].pop(1)
    else:
        coarse = domain_result("resample_market_data", request)[0].model_dump(mode="json")
        request["observations"] = [coarse]
        request["request"]["timeframe"] = "1m"
    before = deepcopy(request)
    with pytest.raises(ValueError):
        domain_result("resample_market_data", request)
    result = tools.resample_market_data(request)
    assert not result.success and result.issues[0].code == "service_input_error"
    assert "secret" not in result.model_dump_json()
    assert request == before


def test_missing_bins_are_omitted_without_filling(inputs):
    request = inputs["resample_market_data"]
    request["observations"].pop(1)
    request["request"]["missing_policy"] = "omit"
    assert tools.resample_market_data(request).value == ()


@pytest.mark.parametrize("case", ["unknown", "duplicates", "registry", "order"])
def test_feature_service_or_contract_rejects_unsupported_requests(inputs, case):
    request = inputs["compute_features"]
    if case == "unknown":
        request["requested_features"][0]["implementation_id"] = "secret-implementation"
    elif case == "duplicates":
        request["requested_features"] *= 2
    elif case == "registry":
        request["registry"] = {"secret": "implementation"}
    else:
        request["bars"].reverse()
    result = tools.compute_features(request)
    assert not result.success and "secret" not in result.model_dump_json()


@pytest.mark.parametrize("limit,action", [("1", risk.RiskAction.REJECT), ("2", risk.RiskAction.ALLOW)])
def test_risk_decision_is_returned_unchanged(inputs, limit, action):
    request = inputs["evaluate_entry_risk"]
    request["config"]["max_position_quantity"] = limit
    result = tools.evaluate_entry_risk(request)
    assert result.success and result.value.action is action
    assert result.value == domain_result("evaluate_entry_risk", request)
    assert result.value.approved_quantity == (0 if action is risk.RiskAction.REJECT else 2)


@pytest.mark.parametrize("case", ["final", "curve", "empty", "ordering"])
def test_analytics_service_rejects_inconsistent_accounting_without_repair(inputs, case):
    request = inputs["analyze_performance"]
    if case == "final":
        request["result"]["final_equity"] = "999"
    elif case == "curve":
        request["result"]["equity_curve"][0]["equity"] = "999"
    elif case == "empty":
        request["result"]["equity_curve"] = []
    else:
        request["result"]["equity_curve"].reverse()
    before = deepcopy(request)
    result = tools.analyze_performance(request)
    assert not result.success and result.issues[0].code == "service_input_error"
    assert request == before


def test_queries_cannot_construct_approval_or_execute_backtests(inputs, monkeypatch):
    from quantlab import backtesting, strategies

    def forbidden(*args, **kwargs):
        raise AssertionError("approval or backtest execution forbidden")

    monkeypatch.setattr(strategies.ApprovalRecord, "__init__", forbidden)
    monkeypatch.setattr(strategies.StrategySpecification, "__init__", forbidden)
    monkeypatch.setattr(backtesting, "run_backtest", forbidden)
    for name in QUERIES:
        assert getattr(tools, name)(inputs[name]).success


@pytest.mark.parametrize("name", QUERIES)
def test_query_results_enforce_success_invariants_and_immutability(name, inputs):
    result = getattr(tools, name)(inputs[name])
    with pytest.raises(ValidationError):
        result.success = False
    for change in ({"success": "true"}, {"value": None}, {"extra": True},
                   {"issues": (AdapterIssue(code="bad", message="Invalid"),)}):
        with pytest.raises(ValidationError):
            RESULTS[name].model_validate(result.model_copy(update=change))


@pytest.mark.parametrize("name", ["validate_market_data", "compute_features", "analyze_performance"])
def test_optional_configuration_uses_existing_service_defaults(name, inputs):
    request = inputs[name]
    request.pop("config", None)
    if name == "compute_features":
        request["bars"] = []
    assert getattr(tools, name)(request).value == domain_result(name, request)


def test_portable_digest_schema_keeps_strict_end_of_string(inputs):
    tool = next(t for t in asyncio.run(build_mcp_server().list_tools())
                if t.name == "analyze_performance")
    request = inputs["analyze_performance"]
    validate({"request": request}, tool.input_schema)
    output = tools.analyze_performance(request).model_dump(mode="json")
    validate(output, tool.output_schema)
    request["result"]["strategy_content_digest"] += "\n"
    with pytest.raises(SchemaValidationError):
        validate({"request": request}, tool.input_schema)
    assert not tools.analyze_performance(request).success
    output["value"]["strategy_content_digest"] += "\n"
    with pytest.raises(SchemaValidationError):
        validate(output, tool.output_schema)
    with pytest.raises(ValidationError):
        PerformanceAnalysisResult.model_validate_json(json.dumps(output))


@pytest.mark.parametrize("name,field", [
    ("validate_market_data", "observations"), ("resample_market_data", "observations"),
    ("compute_features", "bars"),
])
def test_tuples_in_declared_wire_arrays_are_rejected(name, field, inputs):
    request = inputs[name]
    request[field] = tuple(request[field])
    assert getattr(tools, name)(request).issues[0].code == "invalid_json_request"


@pytest.mark.parametrize("timestamp", ["2026-01-01T00:00:00", 0, True])
def test_expected_starts_require_aware_json_timestamps(inputs, timestamp):
    request = inputs["validate_market_data"]
    request["expected_starts"] = [timestamp]
    assert not tools.validate_market_data(request).success


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("name", QUERIES)
def test_nonfinite_decimal_strings_are_domain_failures(name, value, inputs):
    request = inputs[name]
    if name == "validate_market_data":
        request["instrument"]["tick_size"] = value
    elif name == "resample_market_data":
        request["observations"][0]["close"] = value
    elif name == "compute_features":
        request["bars"][0]["close"] = value
    elif name == "evaluate_entry_risk":
        request["context"]["current_equity"] = value
    else:
        request["result"]["final_equity"] = value
    result = getattr(tools, name)(request)
    assert not result.success
    assert any(i.code == "finite_number" for i in result.issues)


@pytest.mark.parametrize("name", QUERIES)
def test_nested_unknown_fields_do_not_leak(name, inputs):
    request = inputs[name]
    target = (request["observations"][0] if name == "validate_market_data" else
              request["request"] if name == "resample_market_data" else
              request["requested_features"][0] if name == "compute_features" else
              request["context"] if name == "evaluate_entry_risk" else
              request["result"]["closed_trades"][0])
    target["secret-key"] = "secret-value"
    result = getattr(tools, name)(request)
    assert not result.success and "secret" not in result.model_dump_json()


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_real_client_server_protocol_lists_and_calls_all_seven_tools(mode, inputs, monkeypatch):
    """Exercise SDK dispatch, not only direct MCPServer method invocation."""
    before = deepcopy(inputs)

    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    async def scenario():
        with monkeypatch.context() as patch:
            for operation in ("connect", "connect_ex", "bind", "listen"):
                patch.setattr(socket.socket, operation, forbidden)
            patch.setattr(socket, "create_connection", forbidden)
            with anyio.fail_after(30):
                async with Client(build_mcp_server(), mode=mode) as client:
                    listed = await client.list_tools()
                    assert [tool.name for tool in listed.tools] == FINAL_TOOL_NAMES
                    for tool in listed.tools:
                        if tool.name not in TOOL_NAMES:
                            continue
                        key = "content" if tool.name == TOOL_NAMES[0] else "request"
                        request = valid_strategy_content() if key == "content" else inputs[tool.name]
                        expected = getattr(tools, tool.name)(request)
                        result = await client.call_tool(tool.name, {key: request})
                        assert not result.is_error
                        assert result.structured_content == expected.model_dump(mode="json")
                        validate(result.structured_content, tool.output_schema)
                    assert not (await client.list_resources()).resources
                    assert not (await client.list_prompts()).prompts

    asyncio.run(scenario())
    assert inputs == before


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("name", TOOL_NAMES)
def test_protocol_failures_are_sanitized_and_do_not_coerce(mode, name, inputs, monkeypatch):
    key = "content" if name == TOOL_NAMES[0] else "request"
    request = valid_strategy_content() if key == "content" else inputs[name]

    async def scenario():
        with anyio.fail_after(30):
            async with Client(build_mcp_server(), mode=mode) as client:
                await client.list_tools()
                for arguments in ({}, {key: request, "secret-key": "secret-value"}):
                    result = await client.call_tool(name, arguments)
                    assert result.is_error and result.structured_content is None
                    assert "Invalid tool arguments." in result.content[0].text
                    assert "secret" not in result.model_dump_json()
                result = await client.call_tool(name, {key: json.dumps(request)})
                assert not result.is_error
                status = "valid" if key == "content" else "success"
                assert result.structured_content[status] is False

                def fail(*args, **kwargs):
                    raise RuntimeError("secret internal detail C:/private/file.py")

                if key == "content":
                    from quantlab.strategies import validation
                    monkeypatch.setattr(validation, "validate_content", fail)
                else:
                    module, service = SERVICES[name]
                    monkeypatch.setattr(module, service, fail)
                result = await client.call_tool(name, {key: request})
                assert result.is_error and result.structured_content is None
                assert result.content[0].text == f"Error executing tool {name}"
                assert "secret" not in result.model_dump_json()

    asyncio.run(scenario())


def test_real_stdio_subprocess_lists_and_calls_all_seven_tools(inputs):
    """Verify the guarded entry point with the SDK's actual stdio transport."""
    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "quantlab.mcp.server"],
        )
        with anyio.fail_after(45):
            async with Client(parameters, mode="legacy") as client:
                listed = await client.list_tools()
                assert [tool.name for tool in listed.tools] == FINAL_TOOL_NAMES
                for tool in listed.tools:
                    if tool.name not in TOOL_NAMES:
                        continue
                    key = "content" if tool.name == TOOL_NAMES[0] else "request"
                    request = valid_strategy_content() if key == "content" else inputs[tool.name]
                    result = await client.call_tool(tool.name, {key: request})
                    assert not result.is_error
                    assert result.structured_content == getattr(tools, tool.name)(request).model_dump(mode="json")
                    validate(result.structured_content, tool.output_schema)

    asyncio.run(scenario())


@pytest.mark.parametrize("name", QUERIES)
def test_published_input_schema_rejects_unknown_tool_and_request_fields(name, inputs):
    tool = next(t for t in asyncio.run(build_mcp_server().list_tools()) if t.name == name)
    request = inputs[name]
    with pytest.raises(SchemaValidationError):
        validate({"request": request, "extra": True}, tool.input_schema)
    request["extra"] = True
    with pytest.raises(SchemaValidationError):
        validate({"request": request}, tool.input_schema)


@pytest.mark.parametrize("name", ["evaluate_entry_risk", "analyze_performance"])
def test_unexpected_value_errors_are_not_application_failures(name, inputs, monkeypatch):
    module, service = SERVICES[name]

    def fail(*args, **kwargs):
        raise ValueError("secret programmer error")

    monkeypatch.setattr(module, service, fail)
    with pytest.raises(ValueError):
        getattr(tools, name)(inputs[name])
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool(name, {"request": inputs[name]}))
    assert str(caught.value) == f"Error executing tool {name}"


@pytest.mark.parametrize("name", QUERIES)
def test_invalid_service_output_remains_an_unexpected_tool_failure(name, inputs, monkeypatch):
    module, service = SERVICES[name]
    monkeypatch.setattr(module, service, lambda *args, **kwargs: object())
    with pytest.raises(ValidationError):
        getattr(tools, name)(inputs[name])
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool(name, {"request": inputs[name]}))
    assert str(caught.value) == f"Error executing tool {name}"
