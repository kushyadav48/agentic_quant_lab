"""Real loopback ASGI socket with explicit graceful process shutdown, no cloud."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import pytest

from tests.api.helpers import settings


def finish_process(process, timeout=15):
    try:
        return process.communicate("stop\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        # Only this test-owned child is terminated after failed graceful exit.
        process.kill()
        process.communicate(timeout=5)
        raise


def test_launcher_keeps_trusted_loopback_and_safe_logging(monkeypatch):
    from quantlab.api import __main__ as launcher
    observed = {}
    monkeypatch.setattr(launcher.APISettings, "from_env", lambda: settings())
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kwargs: observed.update(kwargs))
    launcher.main()
    assert observed["host"] == "127.0.0.1" and observed["workers"] == 1
    assert observed["reload"] is False and observed["access_log"] is False and observed["proxy_headers"] is False


def test_real_process_readiness_auth_restart_and_graceful_shutdown(tmp_path):
    import socket
    from scripts.build_dashboard import stamp
    from quantlab.api import create_app
    directory = tmp_path / "dashboard"
    directory.mkdir()
    (directory / "index.html").write_text('<div id="root"></div>')
    stamp(directory, create_app(settings()).openapi())
    env = dict(os.environ)
    token = secrets.token_urlsafe(48)
    env.update(QUANTLAB_API_OPERATOR_TOKEN=token, QUANTLAB_API_FRONTEND_PATH=str(directory),
        QUANTLAB_API_JOURNAL_PATH=str(tmp_path / "journal.db"), QUANTLAB_CLOUD_ENABLED="false",
        QUANTLAB_CLOUD_FREE_VERIFICATION="", QUANTLAB_API_READER_TOKEN="", QUANTLAB_API_CORS_ORIGINS="")
    # stdin is a test-only supervisor, invoking Uvicorn's normal graceful exit.
    runner = """import threading,sys,uvicorn
from quantlab.api import create_app,APISettings
server=uvicorn.Server(uvicorn.Config(create_app(APISettings.from_env()),host='127.0.0.1',port=int(sys.argv[1]),access_log=False,log_level='warning',proxy_headers=False))
def stop():
    sys.stdin.readline()
    server.should_exit=True
threading.Thread(target=stop,daemon=True).start()
server.run()
"""
    for _ in range(2):
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        process = subprocess.Popen([sys.executable, "-c", runner, str(port)], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        base = f"http://127.0.0.1:{port}"
        def request(path, authenticated=True):
            headers = {"Authorization": "Bearer " + token} if authenticated else {}
            return urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=2)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with request("/api/v1/ready") as response:
                        assert response.status == 200
                    break
                except urllib.error.URLError:
                    assert time.monotonic() < deadline and process.poll() is None
                    time.sleep(0.1)
            with request("/", False) as response:
                assert b'id="root"' in response.read()
            try:
                request("/api/v1/capabilities", False)
                raise AssertionError("anonymous API request admitted")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            with request("/api/v1/ai/configuration") as response:
                assert json.load(response)["state"] == "cloud_disabled"
        finally:
            output, errors = finish_process(process)
        assert process.returncode == 0 and token not in output + errors
    assert (tmp_path / "journal.db").is_file()


def test_failed_shutdown_reaps_only_the_owned_test_process():
    process = subprocess.Popen([sys.executable, "-c",
        "import sys,time; sys.stdin.readline(); time.sleep(30)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            finish_process(process, timeout=0.1)
        assert process.poll() is not None, "Timed-out test child was left running"
    finally:
        # The failing-first probe itself must not leave its test child running.
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
