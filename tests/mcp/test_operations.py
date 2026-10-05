"""Finite lifecycle, canonical identity, concurrency and immutable audit."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
import hashlib
import json
from threading import Event

from pydantic import ValidationError
import pytest

from quantlab import backtesting
from quantlab.mcp import tools
from quantlab.mcp.operation_models import (
    FailureClass, OperationKind, OperationSnapshot, OperationState, OperationSubmission,
)
from quantlab.mcp.operation_tools import OperationTools
from quantlab.mcp.operations import (
    MAX_OPERATIONS, OperationInputError, ResearchOperations, canonical_json, digest_json,
)
from quantlab.strategies import ApprovalState
from .helpers import valid_backtest_request
from .research_helpers import NAMES, requests


def submission(key="run_A", kind="backtest", request=None):
    raw = {"idempotency_key": key,
           "operation": (valid_backtest_request() if request is None else request) | {"kind": kind}}
    return OperationSubmission.model_validate_json(json.dumps(raw), strict=True)


def test_explicit_kinds_and_canonical_serialization():
    assert {kind.value for kind in OperationKind} == {"backtest", "holdout", "walk_forward",
        "robustness", "ml_dataset", "ml_training", "ml_prediction", "ml_prediction_features",
        "performance"}
    with localcontext() as ctx:
        ctx.prec = 2
        assert canonical_json({"x": Decimal("1234.5000"), "z": Decimal("-0.0")}) == '{"x":"1234.5","z":"0"}'
    assert digest_json("{}")== hashlib.sha256(b"{}").hexdigest()


@pytest.mark.parametrize("kind,name", list(zip(
    ["holdout", "walk_forward", "robustness", "ml_dataset", "ml_training", "ml_prediction",
     "ml_prediction_features"], NAMES)))
def test_every_research_kind_runs_existing_adapter(kind, name):
    namespace = ResearchOperations()
    raw = requests()[name]
    queued = namespace.submit(submission(kind=kind, request=raw))
    completed = namespace.execute(queued.operation_id)
    expected = getattr(tools, name)(raw)
    assert completed.state is OperationState.COMPLETED
    assert json.loads(completed.result_json) == json.loads(canonical_json(expected.model_dump(mode="python")))
    assert type(expected).model_validate_json(completed.result_json, strict=True) == expected


def test_lifecycle_immutable_audit_and_terminal_result(monkeypatch):
    namespace = ResearchOperations()
    item = submission()
    queued = namespace.submit(item)
    original = tools.run_backtest
    calls = []
    def run(raw):
        calls.append(raw)
        assert namespace.get(queued.operation_id).state is OperationState.RUNNING
        return original(raw)
    monkeypatch.setattr(tools, "run_backtest", run)
    done = namespace.execute(queued.operation_id)
    assert queued.state is OperationState.QUEUED and len(queued.audit) == 1
    assert [e.state for e in done.audit] == [OperationState.QUEUED, OperationState.RUNNING,
                                           OperationState.COMPLETED]
    assert [e.sequence for e in done.audit] == [1, 2, 3]
    assert all(e.timestamp.utcoffset().total_seconds() == 0 for e in done.audit)
    assert done.result_digest == digest_json(done.result_json)
    assert done.strategies[0].content_digest == item.operation.strategy.content_digest
    assert done.strategies[0].version == item.operation.strategy.version
    assert done.request_digest == digest_json(canonical_json({
        "operation": item.operation.model_dump(mode="python"), "workflow": None}))
    assert namespace.execute(done.operation_id) == done
    assert namespace.submit(item) == done and namespace.get(done.operation_id) == done
    assert len(calls) == 1
    with pytest.raises(OperationInputError):
        namespace.cancel(done.operation_id)
    for value, field, replacement in ((done, "state", OperationState.QUEUED),
        (done.audit[0], "sequence", 8), (done.strategies[0], "version", 9)):
        with pytest.raises(ValidationError):
            setattr(value, field, replacement)
    assert "approval" not in done.model_dump_json()
    assert item.idempotency_key not in done.model_dump_json()
    assert "bars" not in json.dumps([e.model_dump(mode="json") for e in done.audit])


def test_same_key_defaults_key_order_decimal_spelling_and_conflicts():
    namespace = ResearchOperations()
    item = submission()
    first = namespace.submit(item)
    raw = item.model_dump(mode="json")
    raw["operation"].pop("features")
    raw["operation"]["config"]["quantity"] = "2.000"
    raw["operation"] = dict(reversed(list(raw["operation"].items())))
    same = OperationSubmission.model_validate_json(json.dumps(raw), strict=True)
    assert namespace.submit(same) == first
    raw["operation"]["config"]["quantity"] = "3"
    with pytest.raises(OperationInputError) as caught:
        namespace.submit(OperationSubmission.model_validate_json(json.dumps(raw), strict=True))
    assert caught.value.code == "idempotency_conflict"
    with pytest.raises(OperationInputError):
        namespace.submit(submission(kind="holdout", request=requests()["run_holdout"]))
    assert namespace.get(first.operation_id) == first


def test_cancellation_is_before_execution_only_and_never_successful_replay(monkeypatch):
    namespace = ResearchOperations()
    queued = namespace.submit(submission())
    def denied(*args, **kwargs):
        raise AssertionError("cancelled operation executed")
    monkeypatch.setattr(tools, "run_backtest", denied)
    cancelled = namespace.cancel(queued.operation_id)
    assert cancelled.state is OperationState.CANCELLED and cancelled.result_json is None
    assert namespace.cancel(queued.operation_id) == cancelled
    assert namespace.submit(submission()) == cancelled
    with pytest.raises(OperationInputError):
        namespace.execute(queued.operation_id)
    assert len(cancelled.audit) == 2 and namespace.get(queued.operation_id) == cancelled


def test_concurrent_admission_and_execute_have_single_identity_and_service_call(monkeypatch):
    namespace = ResearchOperations()
    item = submission()
    with ThreadPoolExecutor(max_workers=4) as pool:
        admitted = list(pool.map(lambda _: namespace.submit(item), range(8)))
    assert all(value == admitted[0] for value in admitted)
    queued = admitted[0]
    entered, release = Event(), Event()
    original = tools.run_backtest
    def block(raw):
        entered.set()
        assert release.wait(5)
        return original(raw)
    monkeypatch.setattr(tools, "run_backtest", block)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(namespace.execute, queued.operation_id)
        try:
            assert entered.wait(5)
            for method in (namespace.execute, namespace.cancel):
                with pytest.raises(OperationInputError):
                    method(queued.operation_id)
            assert namespace.get(queued.operation_id).state is OperationState.RUNNING
        finally:
            release.set()
        assert future.result().state is OperationState.COMPLETED


@pytest.mark.parametrize("error", [RuntimeError, TypeError, AssertionError, ValueError])
def test_unexpected_failure_cleanup_propagates_and_never_restarts(error, monkeypatch):
    namespace = ResearchOperations()
    queued = namespace.submit(submission())
    calls = []
    def fail(raw):
        calls.append(raw)
        raise error("secret C:/private/file.py")
    monkeypatch.setattr(tools, "run_backtest", fail)
    with pytest.raises(error):
        namespace.execute(queued.operation_id)
    failed = namespace.get(queued.operation_id)
    assert failed.state is OperationState.FAILED and failed.failure is FailureClass.INTERNAL_FAILURE
    assert failed.result_json is None and failed.result_digest is None
    assert "secret" not in failed.model_dump_json() and "private" not in failed.model_dump_json()
    assert namespace.execute(queued.operation_id) == failed and len(calls) == 1
    adapter = OperationTools(namespace)
    with pytest.raises(RuntimeError):
        adapter.execute_research_operation({"operation_id": queued.operation_id})


def test_domain_failure_is_terminal_and_sanitized(monkeypatch):
    raw = valid_backtest_request()
    raw["strategy"]["state"] = "draft"
    raw["strategy"]["approval"] = None
    namespace = ResearchOperations()
    queued = namespace.submit(submission(request=raw))
    assert queued.state is OperationState.QUEUED
    original = backtesting.run_backtest
    calls = []
    def replay(strategy, *args, **kwargs):
        assert strategy.state is ApprovalState.DRAFT and strategy.approval is None
        assert namespace.get(queued.operation_id).state is OperationState.RUNNING
        calls.append(strategy.state)
        return original(strategy, *args, **kwargs)
    monkeypatch.setattr(backtesting, "run_backtest", replay)
    adapter = OperationTools(namespace)
    result = adapter.execute_research_operation({"operation_id": queued.operation_id})
    assert not result.success and result.issues[0].code == "service_input_error"
    failed = namespace.get(queued.operation_id)
    assert failed.state is OperationState.FAILED
    assert [event.state for event in failed.audit] == [
        OperationState.QUEUED, OperationState.RUNNING, OperationState.FAILED,
    ]
    assert failed.failure is FailureClass.DOMAIN_REJECTION and failed.result_json is None
    assert failed.result_digest is None and failed.audit[-1].failure is FailureClass.DOMAIN_REJECTION
    assert result.value is None
    assert result.issues[0].message == "Input rejected by the deterministic service."
    assert "backtesting requires" not in result.model_dump_json() + failed.model_dump_json()
    assert namespace.execute(queued.operation_id) == failed
    assert calls == [ApprovalState.DRAFT]


def test_result_retention_bound_fails_without_partial_success(monkeypatch):
    from quantlab.mcp import operations
    namespace = ResearchOperations()
    queued = namespace.submit(submission())
    monkeypatch.setattr(operations, "MAX_RESULT_BYTES", 8)
    with pytest.raises(RuntimeError):
        namespace.execute(queued.operation_id)
    failed = namespace.get(queued.operation_id)
    assert failed.failure is FailureClass.INTERNAL_FAILURE and failed.result_json is None
    assert namespace.execute(queued.operation_id) == failed


def test_capacity_retains_identities_and_namespace_isolation():
    namespace = ResearchOperations()
    first = namespace.submit(submission("key_0"))
    for index in range(1, MAX_OPERATIONS):
        value = namespace.submit(submission(f"key_{index}"))
        namespace.cancel(value.operation_id)
    assert namespace.submit(submission("key_0")) == first
    with pytest.raises(OperationInputError) as caught:
        namespace.submit(submission("overflow"))
    assert caught.value.code == "operation_capacity"
    with pytest.raises(OperationInputError):
        ResearchOperations().get(first.operation_id)


@pytest.mark.parametrize("method", ["submit_research_operation", "execute_research_operation",
                                  "get_research_operation", "cancel_research_operation"])
@pytest.mark.parametrize("case", ["extra", "string", "nan", "tuple", "cycle"])
def test_operation_wire_sanitization(method, case):
    adapter = OperationTools(ResearchOperations())
    raw = submission().model_dump(mode="json") if method.startswith("submit") else {"operation_id": "0" * 64}
    if case == "string":
        raw = json.dumps(raw)
    elif case == "cycle":
        raw["secret"] = raw
    else:
        raw["secret"] = {"extra": "private", "nan": float("inf"), "tuple": (1,)}[case]
    result = getattr(adapter, method)(raw)
    assert not result.success and "secret" not in result.model_dump_json()


@pytest.mark.parametrize("field,value", [("kind", "arbitrary_job"), ("callback", "code"),
    ("registry", {}), ("evaluator", {}), ("path", "C:/private/file.py"), ("workflow", {})])
def test_submission_cannot_inject_jobs_or_review_claims(field, value):
    raw = submission().model_dump(mode="json")
    raw["operation"][field] = value
    result = OperationTools(ResearchOperations()).submit_research_operation(raw)
    assert not result.success and "private" not in result.model_dump_json()


@pytest.mark.parametrize("state", [OperationState.QUEUED, OperationState.RUNNING, OperationState.CANCELLED])
def test_terminal_audit_chain_cannot_be_rewritten(state):
    namespace = ResearchOperations()
    queued = namespace.submit(submission())
    done = namespace.execute(queued.operation_id)
    raw = done.model_dump(mode="python")
    raw["state"] = state
    with pytest.raises(ValidationError):
        OperationSnapshot.model_validate(raw)


def test_result_digest_and_event_outcome_cannot_be_rewritten():
    namespace = ResearchOperations()
    queued = namespace.submit(submission())
    done = namespace.execute(queued.operation_id)
    raw = done.model_dump(mode="python")
    raw["result_json"] += " "
    with pytest.raises(ValidationError):
        OperationSnapshot.model_validate(raw)
    raw = done.model_dump(mode="python")
    raw["audit"][0]["result_digest"] = done.result_digest
    with pytest.raises(ValidationError):
        OperationSnapshot.model_validate(raw)


def test_stale_or_terminal_transition_cannot_overwrite_result():
    namespace = ResearchOperations()
    queued = namespace.submit(submission())
    done = namespace.execute(queued.operation_id)
    for snapshot in (queued, done):
        with pytest.raises(AssertionError):
            namespace._transition(snapshot, OperationState.CANCELLED)
    assert namespace.get(done.operation_id) == done


def test_performance_kind_captures_existing_result_provenance():
    from tests.analytics.helpers import four_trades
    namespace = ResearchOperations()
    result = four_trades()
    queued = namespace.submit(submission(kind="performance",
        request={"result": result.model_dump(mode="json")}))
    done = namespace.execute(queued.operation_id)
    assert done.state is OperationState.COMPLETED
    expected = tools.analyze_performance({"result": result.model_dump(mode="json")})
    assert type(expected).model_validate_json(done.result_json, strict=True) == expected
    assert done.strategies[0].strategy_id == result.strategy_id
    assert done.strategies[0].version == result.strategy_version
    assert done.strategies[0].content_digest == result.strategy_content_digest
