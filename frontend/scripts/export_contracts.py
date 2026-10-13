"""Export the actual authenticated Phase 21 schema and deterministic test records.

Run from the repository root with an installed project/dev environment. No server,
private data, environment credentials or provider connections are required.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datetime import datetime, timezone
from unittest.mock import patch
from pydantic import SecretStr
from quantlab.api import APISettings, create_app
from tests.api.helpers import client, READ, ROOT
from tests.api.test_reporting import reporting_factory

root = Path(__file__).resolve().parents[1]
settings = APISettings(operator_token=SecretStr("contract-export-only-" + "x" * 32))
schema = create_app(settings).openapi()
(root / "contracts").mkdir(exist_ok=True)
(root / "contracts" / "openapi.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
class FixtureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, tzinfo=timezone.utc)

captured = {}
with patch("quantlab.mcp.operations.datetime", FixtureClock), client(factory=reporting_factory(captured)) as connection:
    paths = {
        "capabilities": "/capabilities",
        "paper": "/paper/sessions/session:a/snapshot",
        "portfolio": "/portfolios/portfolio/snapshot",
        "summary": "/journal/sessions/session:a/summary",
        "operation": "/research/operations/" + captured["operation"].operation_id,
        "result": "/research/operations/" + captured["operation"].operation_id + "/result",
    }
    fixtures = {key: connection.get(ROOT + path, headers=READ).json() for key, path in paths.items()}
    for kind in ("trades", "research"):
        fixtures[kind] = connection.post(ROOT + f"/journal/{kind}/query", json={"limit": 2}, headers=READ).json()
fixtures["source"] = "OFFLINE DEMO — deterministic Python test services; never live or connected account data"
(root / "src" / "test").mkdir(parents=True, exist_ok=True)
(root / "src" / "test" / "fixtures.json").write_text(json.dumps(fixtures, indent=2) + "\n", encoding="utf-8")
(root / "src" / "debugger").mkdir(exist_ok=True)
(root / "src" / "debugger" / "offline.json").write_text(json.dumps({"source": fixtures["source"], "paper": fixtures["paper"]}, indent=2) + "\n", encoding="utf-8")
print("Exported actual OpenAPI and offline test-service fixture")
