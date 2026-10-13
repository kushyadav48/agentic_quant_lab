import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from quantlab.api import APISettings, create_app
from quantlab.api.static import validate_dashboard
from scripts.build_dashboard import stamp
from scripts.check_secrets import inspect
from tests.api.helpers import AUTH, READ, ROOT, settings, admit, OPS


def build(tmp_path):
    directory = tmp_path / "dashboard"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text('<div id="root"></div><script src="/assets/app.js"></script>')
    (directory / "assets/app.js").write_text("console.log('offline fixture')")
    schema = create_app(settings()).openapi()
    stamp(directory, schema)
    return directory, schema


def test_same_origin_static_assets_preserve_auth_and_api_errors(tmp_path):
    directory, schema = build(tmp_path)
    app = create_app(settings(frontend_path=directory))
    assert app.openapi() == schema
    with TestClient(app, base_url="http://localhost") as c:
        assert c.get("/").status_code == 200
        assert c.get("/assets/app.js").status_code == 200
        assert c.get(ROOT + "/capabilities").status_code == 401
        assert c.get(ROOT + "/ready").status_code == 401
        assert c.get(ROOT + "/ready", headers=READ).status_code == 200
        assert c.get("/openapi.json").status_code == 401
        assert c.get("/openapi.json", headers=READ).json() == schema
        assert c.get(ROOT + "/does-not-exist", headers=AUTH).status_code == 404
        for path in ("/.env", "/quantlab-build.json", "/assets/missing.js", "/assets/%2e%2e/.env", "/some-spa-route"):
            assert c.get(path).status_code == 404
        assert c.get("/", headers={"Host": "attacker.example"}).status_code == 400
        assert c.post(OPS, json={}, headers=READ).status_code == 403


@pytest.mark.parametrize("fault", ["missing", "contract", "tamper", "extra", "traversal", "shape"])
def test_build_configuration_fails_closed_without_private_diagnostics(tmp_path, fault):
    directory, schema = build(tmp_path)
    manifest_path = directory / "quantlab-build.json"
    manifest = json.loads(manifest_path.read_text())
    if fault == "missing":
        manifest_path.unlink()
    elif fault == "tamper":
        (directory / "assets/app.js").write_text("private-token")
    elif fault == "extra":
        (directory / ".env").write_text("private-token")
    else:
        if fault == "contract": manifest["contract_sha256"] = "0" * 64
        if fault == "traversal": manifest["files"]["assets/../../private"] = "0" * 64
        if fault == "shape": manifest["files"] = []
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="Invalid dashboard build") as error:
        validate_dashboard(directory, schema)
    assert "private-token" not in str(error.value) and str(tmp_path) not in str(error.value)


def test_invalid_environment_never_echoes_credentials(monkeypatch):
    monkeypatch.setenv("QUANTLAB_API_OPERATOR_TOKEN", "private-invalid-secret")
    with pytest.raises(RuntimeError) as error:
        APISettings.from_env()
    assert "private-invalid-secret" not in str(error.value)


def test_cloud_disabled_restart_has_no_queue_or_financial_owners(tmp_path):
    from quantlab.llm.gemini import GeminiProvider
    directory, _ = build(tmp_path)
    config = settings(frontend_path=directory, journal_path=tmp_path / "journal.db")
    with patch.object(GeminiProvider, "generate", side_effect=AssertionError("provider must stay offline")):
        for attempt in range(2):
            app = create_app(config)
            with TestClient(app, base_url="http://localhost") as c:
                capabilities = c.get(ROOT + "/capabilities", headers=AUTH).json()
                assert capabilities["paper_sessions"] == [] and capabilities["portfolios"] == []
                assert capabilities["journal"] is True
                assert c.get(ROOT + "/ai/configuration", headers=READ).json()["state"] == "cloud_disabled"
                assert app.state.ai.jobs == {} and app.state.ai.tasks == set()
                assert c.post(ROOT + "/journal/trades/query", headers=READ, json={}).json()["records"] == []
                if attempt == 0:
                    operation = admit(c)
                else:
                    assert c.get(OPS + "/" + operation, headers=AUTH).status_code == 404
            assert app.state.ready is False and app.state.lane is None
    assert (tmp_path / "journal.db").is_file()


def test_secret_guard_reports_rules_without_values():
    credential = b"AIza" + b"a" * 35
    assert inspect("frontend/bundle.js", credential) == ["cloud credential"]
    assert inspect(".aws/credentials", b"anything") == ["private/generated file"]
    assert inspect(".env.example", b"GEMINI_API_KEY=\n# empty\n") == []
    assert inspect(".env.example", b"GEMINI_API_KEY=private\n") == ["credential assignment"]
    assert inspect("frontend/.env.production", b"") == ["private/generated file"]


def test_local_build_stamp_rejects_embedded_provider_credential(tmp_path):
    directory, schema = build(tmp_path)
    (directory / "assets/app.js").write_bytes(b"const key='" + b"AIza" + b"a" * 35 + b"';")
    with pytest.raises(ValueError):
        stamp(directory, schema)


def test_ci_policy_and_powershell_syntax():
    from scripts.check_workflows import validate
    root = Path(__file__).resolve().parents[2]
    validate(root / ".github/workflows/ci.yml")
    import os, subprocess
    if os.name == "nt":
        for script in ("start-local.ps1", "check-health.ps1"):
            result = subprocess.run(["powershell", "-NoProfile", "-Command",
                "$errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('" +
                str(root / "scripts" / script) + "',[ref]$null,[ref]$errors); if ($errors.Count) { exit 1 }"], capture_output=True)
            assert result.returncode == 0


def test_ci_application_install_uses_seeded_pinned_backend():
    import yaml
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    checked = 0
    for job in workflow["jobs"].values():
        seeded = False
        for step in job["steps"]:
            command = step.get("run", "")
            if "pip install -r requirements/build.txt" in command:
                seeded = True
            if "pip install" in command and "-e '.[dev]'" in command:
                checked += 1
                assert seeded and "--no-build-isolation" in command
    assert checked == 4
