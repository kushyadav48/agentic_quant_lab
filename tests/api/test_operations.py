from datetime import datetime
import hashlib
import json

import pytest
from quantlab.mcp import tools
from quantlab.mcp.canonical import canonical_json
from tests.mcp.helpers import valid_backtest_request
from tests.mcp.research_helpers import NAMES, requests
from tests.mcp.test_operations import submission
from .helpers import AUTH, ROOT, OPS, client, admit


KINDS = ["holdout", "walk_forward", "robustness", "ml_dataset", "ml_training", "ml_prediction", "ml_prediction_features"]


@pytest.mark.parametrize("kind,name", [("backtest", "run_backtest"), *zip(KINDS, NAMES), ("performance", "analyze_performance")])
def test_all_supported_operation_schemas_and_real_services(kind, name):
    if kind == "backtest":
        raw = valid_backtest_request()
    elif kind == "performance":
        raw = {"result": tools.run_backtest(valid_backtest_request()).value.model_dump(mode="json")}
    else:
        raw = requests()[name]
    with client() as c:
        identity = admit(c, kind=kind, request=raw)
        assert c.get(OPS+"/"+identity+"/result", headers=AUTH).status_code == 409
        done = c.post(OPS+"/"+identity+"/execute", headers=AUTH)
        assert done.status_code == 200 and done.json()["state"] == "completed", done.text
        status = c.get(OPS+"/"+identity, headers=AUTH).json()
        assert [e["state"] for e in status["audit"]] == ["queued", "running", "completed"]
        assert "result_json" not in status
        for event in status["audit"]:
            assert datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
        result = c.get(OPS+"/"+identity+"/result", headers=AUTH).json()
        assert result["kind"] == kind
        assert hashlib.sha256(result["result_json"].encode()).hexdigest() == result["result_digest"] == status["result_digest"]
        expected = getattr(tools, name)(raw)
        assert result["result_json"] == canonical_json(expected.model_dump(mode="python"))
        assert result["result"] == json.loads(result["result_json"])
        assert c.post(OPS+"/"+identity+"/execute", headers=AUTH).json() == status
        assert c.post(OPS+"/"+identity+"/cancel", headers=AUTH).status_code == 409


def test_idempotency_canonical_content_conflicts_and_cancellation(monkeypatch):
    with client() as c:
        identity = admit(c)
        raw = submission().model_dump(mode="json")
        raw["operation"]["config"]["quantity"] = "2.000"
        assert c.post(OPS, json=raw, headers=AUTH).json()["operation_id"] == identity
        raw["operation"]["config"]["quantity"] = "3"
        response = c.post(OPS, json=raw, headers=AUTH)
        assert response.status_code == 409 and response.json()["code"] == "idempotency_conflict"
        cancelled = c.post(OPS+"/"+identity+"/cancel", headers=AUTH).json()
        assert cancelled["state"] == "cancelled"
        monkeypatch.setattr(tools, "run_backtest", lambda _: pytest.fail("Cancelled operation executed"))
        assert c.post(OPS+"/"+identity+"/cancel", headers=AUTH).json() == cancelled
        assert c.post(OPS+"/"+identity+"/execute", headers=AUTH).status_code == 409
        assert c.get(OPS+"/"+identity+"/result", headers=AUTH).status_code == 409


def test_exact_decimal_values_and_utc_normalization():
    raw = valid_backtest_request()
    capital = "1000.12345678901234567890123456789"
    raw["config"]["initial_capital"] = capital
    # Represent the first timestamp using an equivalent non-UTC offset.
    from datetime import timezone, timedelta
    bar = raw["bars"][0]
    for field in ("start_time", "end_time", "available_at"):
        bar[field] = datetime.fromisoformat(bar[field].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
    with client() as c:
        identity = admit(c, request=raw)
        assert c.post(OPS+"/"+identity+"/execute", headers=AUTH).json()["state"] == "completed"
        value = c.get(OPS+"/"+identity+"/result", headers=AUTH).json()["result"]["value"]
        assert value["initial_capital"] == capital
        assert all(datetime.fromisoformat(row["timestamp"]).utcoffset().total_seconds() == 0 for row in value["equity_curve"])


@pytest.mark.parametrize("failure", ["draft", "wrong_digest", "wrong_version", "causal_features"])
def test_approval_and_causality_rejection(failure):
    raw = valid_backtest_request()
    if failure == "draft":
        raw["strategy"]["state"], raw["strategy"]["approval"] = "draft", None
    elif failure == "wrong_digest":
        raw["strategy"]["approval"]["content_digest"] = "0"*64
    elif failure == "wrong_version":
        raw["strategy"]["approval"]["strategy_version"] += 1
    else:
        raw["bars"][1]["start_time"] = raw["bars"][0]["start_time"]
        raw["bars"][1]["end_time"] = raw["bars"][0]["end_time"]
    with client() as c:
        response = c.post(OPS, json={"idempotency_key": "rejected", "operation": raw | {"kind": "backtest"}}, headers=AUTH)
        if response.status_code == 422:
            assert failure in ("wrong_digest", "wrong_version")
        else:
            assert response.status_code == 201
            identity = response.json()["operation_id"]
            status = c.post(OPS+"/"+identity+"/execute", headers=AUTH).json()
            assert status["state"] == "failed" and status["failure"] == "domain_rejection"
            assert c.get(OPS+"/"+identity+"/result", headers=AUTH).status_code == 409


def test_risk_denial_is_retained_and_never_creates_fill():
    raw = valid_backtest_request()
    raw["config"]["risk"] = {"max_position_quantity": "1"}
    with client() as c:
        identity = admit(c, request=raw)
        assert c.post(OPS+"/"+identity+"/execute", headers=AUTH).json()["state"] == "completed"
        value = c.get(OPS+"/"+identity+"/result", headers=AUTH).json()["result"]["value"]
        assert not value["fills"]
        assert value["risk_decisions"] and all(r["action"] == "reject" for r in value["risk_decisions"])


def test_invalid_bounded_requests_and_unknown_ids():
    with client() as c:
        raw = submission().model_dump(mode="json")
        raw["operation"]["bars"] *= 1300
        assert c.post(OPS, json=raw, headers=AUTH).status_code in (413, 422)
        for identity, expected in (("bad", 422), ("0"*64, 404)):
            assert c.get(OPS+"/"+identity, headers=AUTH).status_code == expected
        raw = submission().model_dump(mode="json")
        raw["operation"]["config"]["quantity"] = "1e99999"
        assert c.post(OPS, json=raw, headers=AUTH).status_code == 422
        raw = submission().model_dump(mode="json")
        raw["operation"]["config"]["quantity"] = True
        assert c.post(OPS, json=raw, headers=AUTH).status_code == 422


def test_namespace_capacity_and_app_isolation():
    with client() as c:
        for index in range(32):
            admit(c, key=f"key_{index}")
        response = c.post(OPS, json=submission(key="overflow").model_dump(mode="json"), headers=AUTH)
        assert response.status_code == 503 and response.json()["code"] == "operation_capacity"
        identity = admit(c, key="key_0")
    with client() as fresh:
        assert fresh.get(OPS+"/"+identity, headers=AUTH).status_code == 404
