import json
import logging

import pytest
from pydantic import SecretStr, ValidationError
from quantlab.api import APISettings, create_app
from tests.mcp.helpers import valid_strategy_content
from .helpers import AUTH, READ, ROOT, OPS, OPERATOR, client, settings, admit


@pytest.mark.parametrize("token", ["", "short", "a"*31, "a"*257, "a"*32+" ", "é"*40])
def test_fail_closed_credentials(token):
    with pytest.raises(ValidationError):
        APISettings(operator_token=SecretStr(token))


def test_missing_env_and_identical_roles_fail_closed(monkeypatch):
    monkeypatch.delenv("QUANTLAB_API_OPERATOR_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Invalid API configuration"):
        create_app()
    with pytest.raises(ValidationError):
        APISettings(operator_token=SecretStr(OPERATOR), reader_token=SecretStr(OPERATOR))


@pytest.mark.parametrize("changes", [
    {"operator_token": SecretStr("short")}, {"operator_token": None},
    {"reader_token": SecretStr(OPERATOR)}, {"cors_origins": ("https://example.com",)},
    {"max_request_bytes": 1_048_577}, {"max_concurrent_requests": 0}, {"port": 0},
])
def test_app_revalidates_copied_configuration(changes):
    # Pydantic's copy/update API deliberately skips field validation.
    copied = settings().model_copy(update=changes)
    with pytest.raises(ValidationError):
        create_app(copied)


@pytest.mark.parametrize("origin", ["*", "https://example.com", "http://localhost/", "http://user@localhost:3000", "file://localhost"])
def test_cors_configuration_is_local_and_exact(origin):
    with pytest.raises(ValidationError):
        settings(cors_origins=(origin,))


@pytest.mark.parametrize("method,path", [
    ("GET", ROOT+"/ready"), ("GET", ROOT+"/capabilities"), ("GET", "/openapi.json"),
    ("POST", ROOT+"/strategies/validate"), ("POST", OPS),
    ("GET", OPS+"/"+"0"*64), ("POST", OPS+"/"+"0"*64+"/execute"),
    ("POST", OPS+"/"+"0"*64+"/cancel"), ("GET", OPS+"/"+"0"*64+"/result"),
    ("GET", ROOT+"/paper/sessions/private/snapshot"), ("GET", ROOT+"/portfolios/private/snapshot"),
    ("POST", ROOT+"/journal/trades/query"), ("POST", ROOT+"/journal/research/query"),
    ("GET", ROOT+"/journal/sessions/private/summary"),
])
def test_all_private_routes_authenticate_before_service_or_body(method, path):
    with client() as c:
        response = c.request(method, path)
        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("credential", ["Bearer wrong", "Basic whatever", "Bearer", ""])
def test_invalid_authentication(credential):
    with client() as c:
        assert c.get(ROOT+"/capabilities", headers={"Authorization": credential}).status_code == 401


def test_reader_can_query_and_validate_but_cannot_execute(monkeypatch):
    from quantlab.mcp import tools
    with client() as c:
        identity = admit(c)
        def forbidden(*args):
            raise AssertionError("Unauthorized execution")
        monkeypatch.setattr(tools, "run_backtest", forbidden)
        assert c.get(OPS+"/"+identity, headers=READ).status_code == 200
        assert c.post(ROOT+"/strategies/validate", json={"content": valid_strategy_content()}, headers=READ).json()["valid"]
        for path in (OPS, OPS+"/"+identity+"/execute", OPS+"/"+identity+"/cancel"):
            assert c.post(path, json={}, headers=READ).status_code == 403
        assert c.get(OPS+"/"+identity, headers=AUTH).json()["state"] == "queued"


def test_restrictive_cors_hosts_and_hidden_docs():
    with client(config=settings(cors_origins=("http://localhost:3000",))) as c:
        allowed = c.options(OPS, headers={"Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"})
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
        rejected = c.options(OPS, headers={"Origin": "https://attacker.invalid", "Access-Control-Request-Method": "POST"})
        assert rejected.status_code == 400
        assert "access-control-allow-origin" not in rejected.headers
        assert c.get(ROOT+"/health", headers={"Host": "attacker.invalid"}).status_code == 400
        assert c.get("/docs").status_code == c.get("/redoc").status_code == 404


def test_sanitized_errors_and_structured_correlations(monkeypatch, caplog):
    from quantlab.mcp import tools
    secret = "private-secret-C:/sensitive/database.sqlite"
    with client() as c, caplog.at_level(logging.INFO, logger="quantlab.api"):
        identity = admit(c)
        def fail(*args):
            raise RuntimeError(secret)
        monkeypatch.setattr(tools, "run_backtest", fail)
        response = c.post(OPS+"/"+identity+"/execute", headers=AUTH | {"X-Request-ID": secret})
        assert response.status_code == 500
        assert response.json()["code"] == "internal_error"
        assert response.json()["request_id"] == response.headers["x-request-id"]
        assert len(response.headers["x-request-id"]) == 32
        assert response.headers["cache-control"] == "no-store"
        failed = c.get(OPS+"/"+identity, headers=AUTH).json()
        assert failed["state"] == "failed" and failed["failure"] == "internal_failure"
        assert secret not in response.text+json.dumps(failed)+caplog.text
        assert OPERATOR not in caplog.text
        events = [json.loads(r.message) for r in caplog.records if r.name == "quantlab.api"]
        assert any(e.get("request_id") == response.headers["x-request-id"] for e in events)
        assert all("path" not in e and "payload" not in e for e in events)


@pytest.mark.parametrize("body", [b'{"content":{},"content":{}}', b'{"content":{"a":NaN}}',
    b'{"content":{"a":1.25}}', b'[]', b'{', b'{"content":'+b'['*70+b'0'+b']'*70+b'}'])
def test_strict_json_and_bounded_nesting(body):
    with client() as c:
        assert c.post(ROOT+"/strategies/validate", content=body,
            headers=AUTH | {"Content-Type": "application/json"}).status_code == 422


def test_size_media_and_unknown_keys_are_sanitized():
    with client(config=settings(max_request_bytes=1024)) as c:
        assert c.post(OPS, content=b" "*1025, headers=AUTH).status_code == 413
        assert c.post(OPS, content=b"{}", headers=AUTH | {"Content-Type": "text/plain"}).status_code == 415
        assert c.post(OPS, content=b"{}", headers=AUTH | {"Content-Encoding": "gzip"}).status_code == 415
        assert c.post(OPS, content=b"{}", headers=AUTH | {"Content-Length": "bad"}).status_code == 422
        response = c.post(ROOT+"/journal/trades/query", json={"secret-extra-key": "secret-value"}, headers=AUTH)
        assert response.status_code == 422
        assert "secret" not in response.text
        assert response.json()["issues"][0]["location"] == ["<field>"]


def test_no_http_authority_for_trading_paths():
    with client() as c:
        for path in ("/strategies/approve", "/paper/sessions/create", "/paper/sessions/s/resume",
            "/paper/sessions/s/recover", "/portfolios/create", "/journal/notes", "/broker/orders"):
            assert c.post(ROOT+path, json={}, headers=AUTH).status_code == 404


def test_server_formatter_never_renders_raw_tracebacks_or_credentials():
    from quantlab.api.security import SafeServerFormatter
    record = logging.LogRecord("uvicorn.error", logging.ERROR, "C:/private.py", 1,
        "Traceback: private-credential C:/sensitive.db", (), None)
    rendered = SafeServerFormatter().format(record)
    assert json.loads(rendered) == {"event": "asgi_server", "level": "ERROR"}
    assert "private" not in rendered and "sensitive" not in rendered


def test_environment_loader_and_loopback_launcher(monkeypatch):
    from quantlab.api import __main__ as launcher
    monkeypatch.setenv("QUANTLAB_API_OPERATOR_TOKEN", OPERATOR)
    monkeypatch.delenv("QUANTLAB_API_READER_TOKEN", raising=False)
    monkeypatch.delenv("QUANTLAB_API_JOURNAL_PATH", raising=False)
    monkeypatch.setenv("QUANTLAB_API_CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("QUANTLAB_API_PORT", "8080")
    called = {}
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kwargs: called.update(kwargs))
    monkeypatch.setattr(launcher.logging, "basicConfig", lambda **kwargs: None)
    launcher.main()
    assert called["host"] == "127.0.0.1" and called["port"] == 8080 and called["workers"] == 1
    assert called["reload"] is called["access_log"] is called["proxy_headers"] is False
    assert called["log_config"]["formatters"]["safe"]["()"].endswith("SafeServerFormatter")
    assert OPERATOR not in json.dumps(called)
    monkeypatch.setenv("QUANTLAB_API_PORT", "secret-invalid-port")
    with pytest.raises(RuntimeError, match="Invalid API configuration") as caught:
        APISettings.from_env()
    assert "secret-invalid-port" not in str(caught.value)


@pytest.mark.parametrize("value", ["1e1000000000", "1e-1000000000", "0e1000000000",
    "1e999999999999999999999999999", "9"*4097, " +1e1000000000 ",
    "1_e_1000000000", "١e١٠٠٠٠٠٠٠٠٠"])
def test_sparse_decimal_expansion_is_rejected_before_strategy_digest(value, monkeypatch):
    from quantlab.strategies import StrategyContent
    content = valid_strategy_content()
    content["long"]["entry"]["rules"][0]["right"]["value"] = value
    def forbidden(*args):
        pytest.fail("Unbounded Decimal reached strategy digest")
    monkeypatch.setattr(StrategyContent, "content_digest", forbidden)
    with client() as c:
        response = c.post(ROOT+"/strategies/validate", json={"content": content}, headers=READ)
        assert response.status_code == 422 and response.json()["code"] == "invalid_request"
