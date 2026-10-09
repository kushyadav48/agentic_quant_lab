import asyncio
from threading import Event, get_ident

import httpx
import pytest
from quantlab.api import ApplicationServices, create_app
from quantlab.mcp import tools
from tests.mcp.test_operations import submission
from .helpers import AUTH, ROOT, OPS, client, settings


def test_factory_lifetime_dependency_isolation_and_startup_failure(caplog):
    seen = []
    def factory():
        seen.append(("start", get_ident()))
        return ApplicationServices(close_owners=lambda: seen.append(("close", get_ident())))
    app = create_app(settings(), services_factory=factory)
    assert seen == [] and not app.state.ready
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://localhost") as c:
        assert app.state.ready and c.get(ROOT+"/ready", headers=AUTH).status_code == 200
    assert not app.state.ready and app.state.lane is None
    assert [e[0] for e in seen] == ["start", "close"] and seen[0][1] == seen[1][1] != get_ident()
    def broken():
        raise RuntimeError("secret C:/private.db")
    with pytest.raises(RuntimeError, match="API startup failed") as failure:
        with client(factory=broken):
            pytest.fail("failed startup became available")
    assert "secret" not in str(failure.value)+caplog.text


def test_startup_readiness_failure_closes_owner_and_shutdown_errors_are_sanitized(caplog):
    closed = []
    class BadReady(ApplicationServices):
        def check_ready(self):
            raise RuntimeError("private-ready-secret")
    with pytest.raises(RuntimeError, match="API startup failed"):
        with client(factory=lambda: BadReady(close_owners=lambda: closed.append(True))):
            pass
    assert closed == [True]
    def fail_close():
        raise RuntimeError("private-close-secret")
    with pytest.raises(RuntimeError, match="API shutdown failed"):
        with client(factory=lambda: ApplicationServices(close_owners=fail_close)) as c:
            assert c.get(ROOT+"/ready", headers=AUTH).status_code == 200
    assert "private-ready-secret" not in caplog.text and "private-close-secret" not in caplog.text


def test_configured_journal_is_owned_and_corrupt_startup_fails(tmp_path):
    path = tmp_path/"journal.db"
    with client(config=settings(journal_path=path)) as c:
        assert c.get(ROOT+"/capabilities", headers=AUTH).json()["journal"]
        assert c.post(ROOT+"/journal/trades/query", json={}, headers=AUTH).json()["records"] == []
    broken = tmp_path/"broken.db"
    broken.write_bytes(b"not a sqlite database")
    with pytest.raises(RuntimeError, match="API startup failed"):
        with client(config=settings(journal_path=broken)):
            pass


def test_cancelled_startup_closes_factory_resources_on_service_thread():
    entered, release = Event(), Event()
    seen = []
    def factory():
        seen.append(("start", get_ident()))
        entered.set()
        assert release.wait(5)
        return ApplicationServices(close_owners=lambda: seen.append(("close", get_ident())))
    async def scenario():
        app = create_app(settings(), services_factory=factory)
        context = app.router.lifespan_context(app)
        startup = asyncio.create_task(context.__aenter__())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            startup.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await startup
        assert not app.state.ready and app.state.lane is None
    asyncio.run(scenario())
    assert [event for event, _ in seen] == ["start", "close"]
    assert seen[0][1] == seen[1][1] != get_ident()


def test_cancelled_shutdown_drains_work_and_closes_owners_once():
    entered, release = Event(), Event()
    seen = []
    def factory():
        seen.append(("start", get_ident()))
        return ApplicationServices(close_owners=lambda: seen.append(("close", get_ident())))
    def work():
        entered.set()
        assert release.wait(5)
        seen.append(("work", get_ident()))
    async def scenario():
        app = create_app(settings(), services_factory=factory)
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        lane = app.state.lane
        execution = asyncio.create_task(lane.call(work))
        shutdown = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            shutdown = asyncio.create_task(context.__aexit__(None, None, None))
            while not lane._closing:
                await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        await execution
        assert [event for event, _ in seen] == ["start", "work", "close"]
        # Repeated cleanup must neither recreate resources nor close them twice.
        await lane.close()
        assert not app.state.ready and app.state.lane is None
    asyncio.run(scenario())
    assert [event for event, _ in seen] == ["start", "work", "close"]
    assert len({thread for _, thread in seen}) == 1 and seen[0][1] != get_ident()


def test_without_lifespan_readiness_fails_closed():
    async def scenario():
        app = create_app(settings())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            assert (await c.get(ROOT+"/health")).status_code == 200
            assert (await c.get(ROOT+"/ready", headers=AUTH)).status_code == 503
    asyncio.run(scenario())


@pytest.mark.parametrize("disconnect", [False, True])
def test_running_status_concurrency_disconnect_and_draining_shutdown(monkeypatch, disconnect):
    entered, release = Event(), Event()
    calls, captured = [], {}
    original = tools.run_backtest
    def block(raw):
        calls.append(get_ident())
        entered.set()
        assert release.wait(10)
        return original(raw)
    monkeypatch.setattr(tools, "run_backtest", block)
    def factory():
        services = ApplicationServices(close_owners=lambda: captured.update(closed_thread=get_ident()))
        captured["services"] = services
        return services
    async def scenario():
        app = create_app(settings(), services_factory=factory)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
                response = await c.post(OPS, json=submission().model_dump(mode="json"), headers=AUTH)
                identity = response.json()["operation_id"]
                execution = asyncio.create_task(c.post(OPS+"/"+identity+"/execute", headers=AUTH))
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    running = await c.get(OPS+"/"+identity, headers=AUTH)
                    assert running.json()["state"] == "running"
                    assert (await c.get(ROOT+"/health")).status_code == 200
                    assert (await c.post(OPS+"/"+identity+"/execute", headers=AUTH)).status_code == 503
                    assert (await c.post(OPS+"/"+identity+"/cancel", headers=AUTH)).status_code == 409
                    assert (await c.get(ROOT+"/ready", headers=AUTH)).status_code == 503
                    if disconnect:
                        execution.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await execution
                        assert (await c.post(OPS+"/"+identity+"/execute", headers=AUTH)).status_code == 503
                finally:
                    # Shutdown starts before release; an actual thread drains it.
                    if disconnect:
                        asyncio.get_running_loop().call_later(0.05, release.set)
                    else:
                        release.set()
                        assert (await execution).json()["state"] == "completed"
        snapshot = captured["services"].operations.get(identity)
        assert snapshot.state.value == "completed"
        assert captured["closed_thread"] == calls[0] and len(calls) == 1
    asyncio.run(scenario())


def test_streaming_body_bounds_and_http_concurrency_admission():
    async def scenario():
        app = create_app(settings(max_request_bytes=1024, max_concurrent_requests=1))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
                entered, release = asyncio.Event(), asyncio.Event()
                async def body():
                    yield b" "*1024
                    entered.set()
                    await release.wait()
                    yield b" "
                request = asyncio.create_task(c.post(OPS, content=body(), headers=AUTH))
                await entered.wait()
                assert (await c.get(ROOT+"/health")).status_code == 503
                release.set()
                response = await request
                assert response.status_code == 413
                assert (await c.get(ROOT+"/health")).status_code == 200
    asyncio.run(scenario())


def test_report_output_limit_returns_sanitized_error(monkeypatch):
    from quantlab.api import dependencies
    from tests.api.test_reporting import reporting_factory
    def too_large(*args, **kwargs):
        raise ValueError("Canonical JSON exceeds retention bound: private")
    with client(factory=reporting_factory({})) as c:
        monkeypatch.setattr(dependencies, "canonical_json", too_large)
        response = c.get(ROOT+"/portfolios/portfolio/snapshot", headers=AUTH)
        assert response.status_code == 503 and response.json()["code"] == "response_too_large"
        assert "private" not in response.text


def test_dependency_factory_catalogue_bounds_and_shutdown_close_order():
    from tests.portfolio.helpers import member_owner
    member, owner = member_owner()
    with pytest.raises(ValueError, match="matching identities"):
        ApplicationServices(paper_sessions={"wrong": owner})
    with pytest.raises(ValueError, match="catalogue exceeds bound"):
        ApplicationServices(paper_sessions={str(index): owner for index in range(33)})
    from quantlab.journal import SQLiteJournal
    journal = SQLiteJournal()
    def fail():
        raise RuntimeError("owner close failure")
    services = ApplicationServices(journal=journal, close_owners=fail)
    with pytest.raises(RuntimeError):
        services.close()
    assert journal._closed
