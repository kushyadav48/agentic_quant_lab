"""Phase 22 artifacts bind the current API and actual deterministic service records."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import TypeAdapter
from quantlab.api.schemas import PaperReport, ResearchResult
from quantlab.paper.session_models import SessionSnapshot
from .helpers import client, READ, ROOT
from .test_reporting import reporting_factory

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, tzinfo=timezone.utc)


def test_checked_openapi_matches_current_application():
    with client() as connection:
        assert connection.get("/openapi.json", headers=READ).json() == json.loads(
            (FRONTEND / "contracts/openapi.json").read_text(encoding="utf-8"))


def test_offline_debugger_is_exact_actual_python_service_output():
    expected = json.loads((FRONTEND / "src/debugger/offline.json").read_text(encoding="utf-8"))
    captured = {}
    with patch("quantlab.mcp.operations.datetime", FixtureClock), client(factory=reporting_factory(captured)) as connection:
        actual = connection.get(ROOT + "/paper/sessions/session:a/snapshot", headers=READ).json()
        assert expected["paper"] == actual
        assert "OFFLINE DEMO" in expected["source"]
        assert PaperReport.model_validate_json(json.dumps(actual), strict=True).snapshot == captured["session"]


def test_frontend_fixtures_keep_decimal_serialization_and_result_digest():
    fixtures = json.loads((FRONTEND / "src/test/fixtures.json").read_text(encoding="utf-8"))
    assert isinstance(fixtures["paper"]["snapshot"]["account"]["balance"], str)
    SessionSnapshot.model_validate_json(json.dumps(fixtures["paper"]["snapshot"]), strict=True)
    result = TypeAdapter(ResearchResult).validate_json(json.dumps(fixtures["result"]), strict=True)
    import hashlib
    assert hashlib.sha256(result.result_json.encode("utf-8")).hexdigest() == result.result_digest
