"""Checkpoint 3: canonical replay, exact approval gates and MCP isolation."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json

import anyio
from jsonschema import Draft202012Validator, ValidationError as SchemaValidationError, validate
from mcp.client import Client
from mcp.server.mcpserver.exceptions import UnexpectedToolError
from pydantic import ValidationError
import pytest

from quantlab import backtesting, strategies
from quantlab.data import Instrument, MarketBar
from quantlab.features import FeatureObservation
from quantlab.mcp import build_mcp_server, tools
from quantlab.mcp.models import AdapterIssue, BacktestExecutionRequest, BacktestExecutionResult
from quantlab.risk import RiskAction
from tests.backtesting.helpers import bars, observations, reference, strategy
from .helpers import valid_backtest_request


@pytest.fixture
def replay():
    return valid_backtest_request()


@pytest.fixture
def featured_request(replay):
    spec = strategy(features=(reference(period=1),))
    replay["strategy"] = spec.model_dump(mode="json")
    replay["features"] = [item.model_dump(mode="json") for item in observations(spec, bars())]
    return replay


def canonical(replay):
    return BacktestExecutionRequest.model_validate_json(json.dumps(replay), strict=True)


def direct_result(replay):
    decoded = canonical(replay)
    return backtesting.run_backtest(decoded.strategy, decoded.bars, decoded.features,
                                   instrument=decoded.instrument, config=decoded.config)


def reverse_objects(value):
    if isinstance(value, dict):
        return {key: reverse_objects(item) for key, item in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_objects(item) for item in value]
    return value


def test_json_decodes_canonical_inputs_and_calls_public_service_once(featured_request, monkeypatch):
    replay = featured_request
    before = deepcopy(replay)
    expected = direct_result(replay)
    original = backtesting.run_backtest
    calls = []

    def observe(spec, records, features=(), *, instrument, config):
        calls.append((spec, records, features, instrument, config))
        assert type(spec) is strategies.StrategySpecification
        assert spec.state is strategies.ApprovalState.APPROVED
        assert type(spec.approval) is strategies.ApprovalRecord
        assert spec.approval.content_digest == spec.content_digest
        assert type(records) is tuple and all(type(bar) is MarketBar for bar in records)
        assert type(features) is tuple and all(type(item) is FeatureObservation for item in features)
        assert type(instrument) is Instrument and type(config) is backtesting.BacktestConfig
        assert type(config.quantity) is Decimal
        assert type(records[0].open) is Decimal and records[0].start_time.tzinfo is not None
        return original(spec, records, features, instrument=instrument, config=config)

    monkeypatch.setattr(backtesting, "run_backtest", observe)
    result = tools.run_backtest(replay)
    assert result.success and not result.issues and result.value == expected
    assert len(calls) == 1
    assert replay == before


def test_key_order_determinism_defaults_and_core_python_strictness(replay):
    expected = direct_result(replay)
    decoded = canonical(replay)
    assert decoded.features == ()
    assert decoded.config.execution_costs == backtesting.ExecutionCostConfig()
    assert tools.run_backtest(replay).value == expected
    assert tools.run_backtest(reverse_objects(replay)) == tools.run_backtest(replay)
    replay["features"] = []
    replay["config"] = {"initial_capital": "1000", "quantity": "2"}
    assert tools.run_backtest(replay).value == expected
    with pytest.raises(ValidationError):
        BacktestExecutionRequest.model_validate(replay)
    assert BacktestExecutionRequest.model_validate(decoded) == decoded


def test_supplied_approval_is_only_decoded_never_created_as_an_action(replay, monkeypatch):
    expected = direct_result(replay)

    def forbidden(*args, **kwargs):
        raise AssertionError("approval/specification construction or transition forbidden")

    # Pydantic decodes the already-supplied record through its schema, not these
    # action/construction APIs. The adapter contains no ApprovalRecord constructor.
    monkeypatch.setattr(strategies.ApprovalRecord, "__init__", forbidden)
    monkeypatch.setattr(strategies.StrategySpecification, "__init__", forbidden)
    for method in ("approve", "mark_validated", "revise"):
        monkeypatch.setattr(strategies.StrategySpecification, method, forbidden)
    assert tools.run_backtest(replay).value == expected


@pytest.mark.parametrize("state", ["draft", "validated"])
def test_unapproved_specifications_reach_the_existing_service_gate(replay, state, monkeypatch):
    replay["strategy"]["state"] = state
    replay["strategy"]["approval"] = None
    assert canonical(replay).strategy.state.value == state
    with pytest.raises(backtesting.BacktestCompatibilityError):
        direct_result(replay)
    original = backtesting.run_backtest
    calls = []

    def observe(*args, **kwargs):
        calls.append(args[0].state)
        return original(*args, **kwargs)

    monkeypatch.setattr(backtesting, "run_backtest", observe)
    before = deepcopy(replay)
    result = tools.run_backtest(replay)
    assert not result.success and result.value is None
    assert result.issues[0].code == "service_input_error"
    assert len(calls) == 1 and replay == before


@pytest.mark.parametrize("case", [
    "missing_record", "boolean_record", "digest", "id", "version", "content",
    "draft_with_record", "rejected", "raw_content", "approved_flag", "digest_only",
])
def test_malformed_or_substitute_approval_cannot_reach_backtester(replay, case, monkeypatch):
    spec = replay["strategy"]
    if case == "missing_record":
        spec.pop("approval")
    elif case == "boolean_record":
        spec["approval"] = True
    elif case == "digest":
        spec["approval"]["content_digest"] = "0" * 64
    elif case == "id":
        spec["approval"]["strategy_id"] = "other"
    elif case == "version":
        spec["version"] += 1
    elif case == "content":
        spec["content"]["name"] = "changed after approval"
    elif case == "draft_with_record":
        spec["state"] = "draft"
    elif case == "rejected":
        spec["state"] = "rejected"
    elif case == "raw_content":
        replay["strategy"] = spec["content"]
    elif case == "approved_flag":
        replay["strategy"] = {"content": spec["content"], "approved": True}
    else:
        replay["strategy"] = {"content_digest": spec["approval"]["content_digest"]}
    before = deepcopy(replay)

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid approval must not reach backtester")

    monkeypatch.setattr(backtesting, "run_backtest", forbidden)
    result = tools.run_backtest(replay)
    assert not result.success and result.issues and result.value is None
    assert replay == before


@pytest.mark.parametrize("field", ["strategy", "bars", "instrument", "config"])
def test_required_canonical_inputs_cannot_be_invented(replay, field, monkeypatch):
    del replay[field]
    monkeypatch.setattr(backtesting, "run_backtest", lambda *args, **kwargs: pytest.fail("service called"))
    result = tools.run_backtest(replay)
    assert not result.success and any(issue.code == "missing" for issue in result.issues)


@pytest.mark.parametrize("where", [
    "root", "strategy", "approval", "content", "operand", "bar", "instrument",
    "config", "risk", "costs", "feature",
])
def test_unknown_fields_are_rejected_and_sanitized(featured_request, where, monkeypatch):
    replay = featured_request
    spec = replay["strategy"]
    targets = {
        "root": replay, "strategy": spec, "approval": spec["approval"], "content": spec["content"],
        "operand": spec["content"]["long"]["entry"]["rules"][0]["left"],
        "bar": replay["bars"][0], "instrument": replay["instrument"], "config": replay["config"],
        "risk": replay["config"]["risk"], "costs": replay["config"]["execution_costs"],
        "feature": replay["features"][0],
    }
    targets[where]["secret-key"] = "secret-value C:/private/file.py"
    before = deepcopy(replay)
    monkeypatch.setattr(backtesting, "run_backtest", lambda *args, **kwargs: pytest.fail("service called"))
    result = tools.run_backtest(replay)
    assert not result.success and "secret" not in result.model_dump_json()
    assert any(issue.code == "extra_forbidden" for issue in result.issues)
    assert result == tools.run_backtest(reverse_objects(replay))
    assert replay == before


@pytest.mark.parametrize("field", [
    "registry", "feature_registry", "evaluator", "rule_evaluator", "engine",
    "execution_engine", "callback", "code", "source_code", "approved",
])
def test_executable_and_authority_injection_fields_are_forbidden(replay, field, monkeypatch):
    replay[field] = {"implementation": "secret-code"}
    monkeypatch.setattr(backtesting, "run_backtest", lambda *args, **kwargs: pytest.fail("service called"))
    result = tools.run_backtest(replay)
    assert not result.success and "secret" not in result.model_dump_json()
    assert any(issue.code == "extra_forbidden" for issue in result.issues)


@pytest.mark.parametrize("bad", [
    None, [], "{}", {1: "secret"}, {"secret": (1,)}, {"secret": b"secret"},
    {"secret": Decimal("1")}, {"secret": object()}, {"secret": lambda: None},
    {"secret": Instrument}, {"secret": float("nan")}, {"secret": float("inf")},
    {"secret": float("-inf")},
])
def test_non_json_inputs_are_safely_rejected_by_adapter_and_sdk(bad, monkeypatch):
    monkeypatch.setattr(backtesting, "run_backtest", lambda *args, **kwargs: pytest.fail("service called"))
    expected = tools.run_backtest(bad)
    assert not expected.success and expected.value is None
    assert [issue.code for issue in expected.issues] == ["invalid_json_request"]
    assert "secret" not in expected.model_dump_json()
    result = asyncio.run(build_mcp_server().call_tool("run_backtest", {"request": bad}))
    assert not result.is_error and result.structured_content == expected.model_dump(mode="json")


def test_cycles_and_python_tuples_are_not_coerced(replay):
    replay["secret-cycle"] = replay
    result = asyncio.run(build_mcp_server().call_tool("run_backtest", {"request": replay}))
    assert result.structured_content["issues"][0]["code"] == "invalid_json_request"
    assert "secret" not in result.model_dump_json()
    replay = valid_backtest_request()
    replay["bars"] = tuple(replay["bars"])
    assert tools.run_backtest(replay).issues[0].code == "invalid_json_request"


@pytest.mark.parametrize("field,value", [
    ("quantity", True), ("quantity", "secret-decimal"), ("quantity", "NaN"),
    ("quantity", "Infinity"), ("quantity", "-Infinity"),
    ("version", True), ("direction", "secret-enum"), ("state", "secret-enum"),
    ("start_time", "secret-time"), ("start_time", "2026-01-01T00:00:00"),
    ("reviewed_at", "2026-01-01T00:00:00"), ("reviewed_at", "secret-time"),
    ("close", "secret-decimal"), ("close", "NaN"), ("close", "Infinity"),
    ("close", "-Infinity"),
])
def test_malformed_domain_values_are_rejected_without_repair(replay, field, value, monkeypatch):
    target = (replay["config"] if field == "quantity" else
              replay["strategy"] if field in ("version", "state") else
              replay["strategy"]["content"] if field == "direction" else
              replay["strategy"]["approval"] if field == "reviewed_at" else replay["bars"][0])
    target[field] = value
    before = deepcopy(replay)
    with pytest.raises(ValidationError):
        canonical(replay)
    monkeypatch.setattr(backtesting, "run_backtest", lambda *args, **kwargs: pytest.fail("service called"))
    result = tools.run_backtest(replay)
    assert not result.success and "secret" not in result.model_dump_json()
    assert replay == before


@pytest.mark.parametrize("case", ["empty", "order", "duplicate", "quantity", "instrument", "spread"])
def test_existing_service_rejects_incompatible_replay_inputs(replay, case):
    if case == "empty":
        replay["bars"] = []
    elif case == "order":
        replay["bars"].reverse()
    elif case == "duplicate":
        replay["bars"].insert(0, deepcopy(replay["bars"][0]))
    elif case == "quantity":
        replay["config"]["quantity"] = "1.5"
    elif case == "instrument":
        replay["instrument"]["instrument_id"] = "other"
    else:
        replay["config"]["execution_costs"]["spread"] = "2"
    before = deepcopy(replay)
    with pytest.raises(backtesting.BacktestError):
        direct_result(replay)
    result = tools.run_backtest(replay)
    assert not result.success and result.issues[0].code == "service_input_error"
    assert replay == before


@pytest.mark.parametrize("case", ["order", "duplicate", "implementation", "provenance"])
def test_feature_input_semantics_remain_service_owned(featured_request, case):
    replay = featured_request
    if case == "order":
        replay["features"].reverse()
    elif case == "duplicate":
        replay["features"].append(deepcopy(replay["features"][-1]))
    elif case == "implementation":
        replay["features"][0]["implementation_id"] = "secret-implementation"
    else:
        # Valid observation timestamps, but claimed history omits its ending bar.
        replay["features"][1]["input_start"] = (bars()[1].start_time + timedelta(seconds=30)).isoformat()
    with pytest.raises(backtesting.BacktestInputError):
        direct_result(replay)
    result = tools.run_backtest(replay)
    assert not result.success and "secret" not in result.model_dump_json()


@pytest.mark.parametrize("with_costs", [False, True])
def test_replay_returns_service_fills_and_hand_calculated_accounting(replay, with_costs):
    replay["bars"] = [item.model_dump(mode="json") for item in
                       bars((101, 110, 99, 90), (80, 100, 111, 90))]
    if with_costs:
        for bar in replay["bars"]:
            bar["price_type"] = "mid"
        replay["config"]["execution_costs"] = {
            "spread": "2", "slippage": "0.5", "commission_per_unit": "0.25", "fixed_fee_per_fill": "1",
        }
    result = tools.run_backtest(replay)
    assert result.success and result.value == direct_result(replay)
    value = result.value
    assert value.final_equity == Decimal("971" if with_costs else "980")
    assert value.closed_trades[0].net_pnl == Decimal("-29" if with_costs else "-20")
    assert len(value.signals) == len(value.fills) == 2 and value.open_position is None
    assert value.fills[0].reference_price == Decimal("100")
    assert value.fills[1].reference_price == Decimal("90")
    assert value.fills[0].execution_time == canonical(replay).bars[1].start_time
    assert value.fills[1].execution_time == canonical(replay).bars[3].start_time


def test_risk_reject_remains_reject_with_no_fills_costs_or_pnl(replay):
    replay["config"]["risk"] = {"max_position_quantity": "1"}
    replay["config"]["execution_costs"] = {"slippage": "0.5", "fixed_fee_per_fill": "1"}
    result = tools.run_backtest(replay)
    assert result.success and result.value == direct_result(replay)
    value = result.value
    assert value.signals and value.risk_decisions
    assert all(decision.action is RiskAction.REJECT and decision.approved_quantity == 0
               for decision in value.risk_decisions)
    assert value.fills == value.closed_trades == () and value.open_position is None
    assert value.realized_pnl == value.unrealized_pnl == 0
    assert value.final_equity == Decimal("1000")
    assert all(point.equity == Decimal("1000") for point in value.equity_curve)


def test_exits_bypass_entry_risk_and_open_positions_are_not_forced_closed(replay):
    replay["bars"] = [item.model_dump(mode="json") for item in bars((101, 50, 50), (80, 100, 40))]
    replay["config"]["risk"] = {"minimum_equity": "950", "max_drawdown_fraction": "0.01"}
    value = tools.run_backtest(replay).value
    assert value == direct_result(replay)
    assert len(value.risk_decisions) == 1 and value.risk_decisions[0].action is RiskAction.ALLOW
    assert len(value.fills) == 2 and value.final_equity == Decimal("880")
    replay["strategy"] = strategy(no_exit=True).model_dump(mode="json")
    value = tools.run_backtest(replay).value
    assert value == direct_result(replay)
    assert value.open_position is not None and value.closed_trades == ()


def test_delayed_retrospective_marks_do_not_change_causal_risk_peaks(replay):
    series = list(bars((101, 200, 99, 101, 110), (80, 100, 100, 100, 100)))
    series[1] = series[1].model_copy(update={"available_at": series[3].end_time})
    replay["bars"] = [item.model_dump(mode="json") for item in series]
    replay["config"]["risk"] = {"max_drawdown_fraction": "0.10"}
    value = tools.run_backtest(replay).value
    assert value == direct_result(replay)
    assert value.equity_curve[1].equity == Decimal("1200")
    assert [decision.action for decision in value.risk_decisions] == [RiskAction.ALLOW, RiskAction.ALLOW]
    assert value.risk_decisions[-1].running_peak_equity == Decimal("1000")


@pytest.mark.parametrize("error", [
    backtesting.BacktestInputError, backtesting.BacktestCompatibilityError, backtesting.BacktestSignalConflictError,
])
def test_expected_public_errors_return_sanitized_failures(replay, error, monkeypatch):
    def reject(*args, **kwargs):
        raise error("secret internal detail C:/private/file.py")

    monkeypatch.setattr(backtesting, "run_backtest", reject)
    result = tools.run_backtest(replay)
    assert not result.success and result.value is None
    assert result.issues == (AdapterIssue(code="service_input_error",
                                        message="Input rejected by the deterministic service."),)
    sdk = asyncio.run(build_mcp_server().call_tool("run_backtest", {"request": replay}))
    assert not sdk.is_error and sdk.structured_content == result.model_dump(mode="json")


@pytest.mark.parametrize("error", [RuntimeError, TypeError, ValueError, AssertionError])
def test_unexpected_failures_propagate_as_sanitized_sdk_failures(replay, error, monkeypatch):
    def fail(*args, **kwargs):
        raise error("secret programmer detail C:/private/file.py")

    monkeypatch.setattr(backtesting, "run_backtest", fail)
    with pytest.raises(error):
        tools.run_backtest(replay)
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool("run_backtest", {"request": replay}))
    assert str(caught.value) == "Error executing tool run_backtest"


@pytest.mark.parametrize("output", [None, object(), {"secret": "not a result"}])
def test_invalid_service_output_is_never_repaired_or_reported_as_input_failure(replay, output, monkeypatch):
    monkeypatch.setattr(backtesting, "run_backtest", lambda *args, **kwargs: output)
    with pytest.raises(ValidationError):
        tools.run_backtest(replay)
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool("run_backtest", {"request": replay}))
    assert str(caught.value) == "Error executing tool run_backtest"


@pytest.mark.parametrize("case", ["valid", "domain", "service"])
def test_actual_sdk_input_and_output_schemas_and_results(replay, case):
    if case == "domain":
        replay["strategy"]["approval"]["content_digest"] = "0" * 64
    elif case == "service":
        replay["bars"] = []
    before = deepcopy(replay)
    expected = tools.run_backtest(replay)

    async def scenario():
        server = build_mcp_server()
        tool = next(item for item in await server.list_tools() if item.name == "run_backtest")
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
        if case == "valid":
            validate({"request": replay}, tool.input_schema)
        result = await server.call_tool("run_backtest", {"request": replay})
        assert not result.is_error and result.structured_content == expected.model_dump(mode="json")
        validate(result.structured_content, tool.output_schema)
        assert json.loads(result.content[0].text) == result.structured_content
        assert BacktestExecutionResult.model_validate_json(json.dumps(result.structured_content)) == expected

    asyncio.run(scenario())
    assert replay == before


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("case", ["unapproved", "unknown_field", "incompatible"])
def test_real_client_reports_expected_replay_rejections_without_leaking(replay, mode, case):
    if case == "unapproved":
        replay["strategy"]["state"] = "draft"
        replay["strategy"]["approval"] = None
    elif case == "unknown_field":
        replay["strategy"]["secret-key"] = "secret-value"
    else:
        replay["config"]["quantity"] = "1.5"
    before = deepcopy(replay)
    expected = tools.run_backtest(replay)

    async def scenario():
        with anyio.fail_after(30):
            async with Client(build_mcp_server(), mode=mode) as client:
                listed = await client.list_tools()
                tool = next(item for item in listed.tools if item.name == "run_backtest")
                result = await client.call_tool("run_backtest", {"request": replay})
                assert not result.is_error
                assert result.structured_content == expected.model_dump(mode="json")
                assert not result.structured_content["success"] and result.structured_content["value"] is None
                validate(result.structured_content, tool.output_schema)
                assert "secret" not in result.model_dump_json()

    asyncio.run(scenario())
    assert not expected.success and replay == before


def test_schemas_forbid_injection_and_preserve_strict_digest_endings(replay):
    tool = next(item for item in asyncio.run(build_mcp_server().list_tools()) if item.name == "run_backtest")
    with pytest.raises(SchemaValidationError):
        validate({"request": replay, "registry": {}}, tool.input_schema)
    replay["registry"] = {}
    with pytest.raises(SchemaValidationError):
        validate({"request": replay}, tool.input_schema)
    del replay["registry"]
    output = tools.run_backtest(replay).model_dump(mode="json")
    replay["strategy"]["approval"]["content_digest"] += "\n"
    with pytest.raises(SchemaValidationError):
        validate({"request": replay}, tool.input_schema)
    output["value"]["strategy_content_digest"] += "\n"
    with pytest.raises(SchemaValidationError):
        validate(output, tool.output_schema)


def test_results_are_frozen_and_enforce_completion_semantics(replay):
    result = tools.run_backtest(replay)
    with pytest.raises(ValidationError):
        result.success = False
    for change in ({"success": "true"}, {"value": None}, {"extra": True},
                   {"issues": (AdapterIssue(code="bad", message="Invalid"),)}):
        with pytest.raises(ValidationError):
            BacktestExecutionResult.model_validate(result.model_copy(update=change))
