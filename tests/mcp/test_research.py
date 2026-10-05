"""Phase 10/12 service reuse, strict wire contracts and unchanged authority."""
import asyncio
from copy import deepcopy
import json

from jsonschema import Draft202012Validator, validate
from mcp.server.mcpserver.exceptions import UnexpectedToolError
from pydantic import ValidationError
import pytest

from quantlab import ml, validation
from quantlab.mcp import build_mcp_server, tools
from quantlab.mcp import research_models as contracts
from quantlab.strategies import ApprovalRecord, StrategySpecification
from .research_helpers import NAMES, requests

CONTRACTS = dict(zip(NAMES, (contracts.HoldoutRequest, contracts.WalkForwardRequest,
    contracts.RobustnessRequest, contracts.DatasetRequest, contracts.TrainingRequest,
    contracts.PredictionRequest, contracts.PredictionFeaturesRequest)))
SERVICES = dict(zip(NAMES, (
    (validation, "run_holdout"), (validation, "run_walk_forward"),
    (validation, "run_parameter_robustness"), (ml, "build_dataset"), (ml, "train_model"),
    (ml, "predict_oos"), (ml, "predictions_to_features"))))


@pytest.mark.parametrize("name", NAMES)
def test_canonical_public_service_once_and_unchanged_result(name, monkeypatch):
    raw = requests()[name]
    before = deepcopy(raw)
    service, attribute = SERVICES[name]
    original = getattr(service, attribute)
    observed = []
    def run(*args, **kwargs):
        assert not {"registry", "evaluator", "engine", "callback", "estimator"} & set(kwargs)
        assert not any(isinstance(value, (dict, list)) for value in args)
        result = original(*args, **kwargs)
        observed.append(result)
        return result
    monkeypatch.setattr(service, attribute, run)
    result = getattr(tools, name)(raw)
    assert result.success and observed == [result.value]
    assert raw == before
    assert CONTRACTS[name].model_validate_json(json.dumps(raw), strict=True)
    with pytest.raises(ValidationError):
        CONTRACTS[name].model_validate(raw)
    with pytest.raises(ValidationError):
        result.success = False


@pytest.mark.parametrize("name", NAMES)
def test_schemas_and_sdk_results(name):
    raw = requests()[name]
    expected = getattr(tools, name)(raw)
    async def scenario():
        server = build_mcp_server()
        tool = next(t for t in await server.list_tools() if t.name == name)
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
        validate({"request": raw}, tool.input_schema)
        result = await server.call_tool(name, {"request": raw})
        assert result.structured_content == expected.model_dump(mode="json")
        validate(result.structured_content, tool.output_schema)
    asyncio.run(scenario())


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("case", ["extra", "nested", "string", "nan", "tuple", "bytes", "object", "cycle"])
def test_strict_wire_rejects_without_echo_or_repair(name, case):
    raw = requests()[name]
    if case == "extra":
        raw["secret-path"] = "C:/private/file.py"
    elif case == "nested":
        next(value for value in raw.values() if isinstance(value, dict))["secret"] = "private"
    elif case == "string":
        raw = json.dumps(raw)
    elif case == "tuple":
        raw = {"secret": (1,)}
    elif case == "cycle":
        raw["cycle"] = raw
    else:
        raw["secret"] = {"nan": float("nan"), "bytes": b"private", "object": object()}[case]
    result = getattr(tools, name)(raw)
    assert not result.success and result.value is None
    assert "secret" not in result.model_dump_json() and "private" not in result.model_dump_json()


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("error", [RuntimeError, TypeError, AssertionError, ValueError])
def test_only_public_input_error_family_is_caught(name, error, monkeypatch):
    raw = requests()[name]
    module, attribute = SERVICES[name]
    def fail(*args, **kwargs):
        raise error("secret C:/private/file.py")
    monkeypatch.setattr(module, attribute, fail)
    with pytest.raises(error):
        getattr(tools, name)(raw)
    with pytest.raises(UnexpectedToolError) as caught:
        asyncio.run(build_mcp_server().call_tool(name, {"request": raw}))
    assert str(caught.value) == f"Error executing tool {name}"


@pytest.mark.parametrize("name", NAMES)
def test_expected_rejection_and_invalid_output(name, monkeypatch):
    raw = requests()[name]
    module, attribute = SERVICES[name]
    error = validation.ResearchValidationError if module is validation else ml.MLResearchError
    def fail(*args, **kwargs):
        raise error("secret C:/private/file.py")
    monkeypatch.setattr(module, attribute, fail)
    result = getattr(tools, name)(raw)
    assert not result.success and result.issues[0].code == "service_input_error"
    assert "secret" not in result.model_dump_json()
    monkeypatch.setattr(module, attribute, lambda *args, **kwargs: {"secret": "invalid result"})
    with pytest.raises(ValidationError):
        getattr(tools, name)(raw)


def test_research_approval_and_ml_leakage_gates_are_preserved(monkeypatch):
    inputs = requests()
    for name in NAMES[:3]:
        raw = deepcopy(inputs[name])
        strategy = raw.get("strategy", raw.get("candidates", [{}])[0].get("strategy"))
        strategy["state"] = "DRAFT"
        strategy["approval"] = None
        assert not getattr(tools, name)(raw).success
    raw = inputs["predict_ml_oos"]
    raw["dataset"] = inputs["train_ml_model"]["dataset"]
    assert not tools.predict_ml_oos(raw).success
    def denied(*args, **kwargs):
        raise AssertionError("approval authority forbidden")
    monkeypatch.setattr(ApprovalRecord, "__init__", denied)
    for attribute in ("__init__", "approve", "mark_validated", "revise"):
        monkeypatch.setattr(StrategySpecification, attribute, denied)
    for name, raw in inputs.items():
        # The one intentionally labeled inference input remains rejected.
        assert getattr(tools, name)(raw).success is (name != "predict_ml_oos")


def test_admission_bounds_and_unsupported_estimators():
    inputs = requests()
    raw = inputs["run_walk_forward"]
    raw["bars"] *= 1300
    assert not tools.run_walk_forward(raw).success
    raw = inputs["train_ml_model"]
    raw["config"] = {"model_type": "arbitrary-estimator", "alpha": "1"}
    assert not tools.train_ml_model(raw).success
    raw = inputs["build_ml_dataset"]
    raw["feature_schema"]["features"] = [
        {"feature_id": f"x{i}", "implementation_id": "close"} for i in range(33)]
    assert not tools.build_ml_dataset(raw).success


@pytest.mark.parametrize("field", ["registry", "evaluator", "engine", "callback", "code",
                                   "path", "estimator", "pickle", "joblib"])
@pytest.mark.parametrize("name", NAMES)
def test_executable_and_io_dependency_fields_are_never_accepted(name, field):
    raw = requests()[name]
    raw[field] = "secret C:/private/file.py"
    result = getattr(tools, name)(raw)
    assert not result.success and "secret" not in result.model_dump_json()


def test_oos_and_prediction_provenance_remain_service_owned():
    from tests.ml.helpers import dataset
    raw = requests()["predict_ml_oos"]
    raw["dataset"] = dataset(start=0, end=4, target=None).model_dump(mode="json")
    assert not tools.predict_ml_oos(raw).success
    raw = requests()["ml_predictions_to_features"]
    raw["predictions"][0]["model_digest"] = "0" * 64
    assert not tools.ml_predictions_to_features(raw).success
    raw = requests()["run_parameter_robustness"]
    from tests.backtesting.helpers import strategy
    raw["candidates"].append({"candidate_id": "changed_structure",
                               "strategy": strategy(name="different structure").model_dump(mode="json")})
    assert not tools.run_parameter_robustness(raw).success


def test_admission_rejects_before_calling_service(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("out-of-bounds service invocation")
    inputs = requests()
    raw = inputs["train_ml_model"]
    raw["config"] = {"alpha": "1e10000000"}
    monkeypatch.setattr(ml, "train_model", denied)
    assert not tools.train_ml_model(raw).success
    raw = inputs["run_walk_forward"]
    from tests.backtesting.helpers import bars
    raw["bars"] = [bar.model_dump(mode="json") for bar in bars(tuple(range(100, 200)))]
    raw["walk_forward"] = {"train_size": 1, "test_size": 1, "step_size": 1}
    monkeypatch.setattr(validation, "run_walk_forward", denied)
    assert not tools.run_walk_forward(raw).success
