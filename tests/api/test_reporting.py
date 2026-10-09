import json
from threading import get_ident

import pytest
from quantlab.api import ApplicationServices
from quantlab.journal import JournalSession, SQLiteJournal
from quantlab.mcp.operations import ResearchOperations
from tests.mcp.test_operations import submission
from tests.portfolio.helpers import member_owner, portfolio, enroll, observe, entry_inputs
from .helpers import AUTH, READ, ROOT, client


def reporting_factory(captured, path=":memory:"):
    def factory():
        captured["thread"] = get_ident()
        member, owner = member_owner()
        p = portfolio()
        enroll(p, member)
        journal = SQLiteJournal(path)
        journal.register_session(JournalSession(config=owner.config, strategy=member.strategy,
            admission=owner.snapshot.runtime.admission))
        for item in entry_inputs():
            result = owner.process(item)
            observe(p, member, result)
            journal.ingest_trade(result)
        operations = ResearchOperations()
        queued = operations.submit(submission())
        completed = operations.execute(queued.operation_id)
        journal.ingest_research("api-fixture", completed)
        captured.update(session=owner.snapshot, portfolio=p.snapshot, journal=journal, operation=completed)
        def close():
            captured["close_thread"] = get_ident()
        return ApplicationServices(operations=operations, paper_sessions={owner.config.strategy.session_id: owner},
            portfolios={p.snapshot.config.portfolio_id: p}, journal=journal, close_owners=close)
    return factory


def test_paper_portfolio_and_journal_use_actual_committed_services(tmp_path):
    captured = {}
    path = tmp_path/"journal.db"
    with client(factory=reporting_factory(captured, path)) as c:
        capabilities = c.get(ROOT+"/capabilities", headers=READ).json()
        assert capabilities["paper_sessions"] == ["session:a"] and capabilities["portfolios"] == ["portfolio"]
        paper = c.get(ROOT+"/paper/sessions/session:a/snapshot", headers=READ)
        assert paper.status_code == 200
        from quantlab.paper.session_models import SessionSnapshot
        assert SessionSnapshot.model_validate_json(json.dumps(paper.json()["snapshot"]), strict=True) == captured["session"]
        assert paper.json()["operator_required"] is False
        portfolio_report = c.get(ROOT+"/portfolios/portfolio/snapshot", headers=READ)
        assert portfolio_report.status_code == 200
        from quantlab.portfolio import PortfolioSnapshot
        assert PortfolioSnapshot.model_validate_json(portfolio_report.content, strict=True) == captured["portfolio"]
        summary = c.get(ROOT+"/journal/sessions/session:a/summary", headers=READ)
        assert summary.status_code == 200
        assert summary.json()["lifecycle"] == "open" and summary.json()["closed_outcome"] is None
        assert summary.json()["account"] == paper.json()["snapshot"]["account"]
        pages, cursor = [], None
        while True:
            query = {"session_id": "session:a", "limit": 2, "after": cursor}
            response = c.post(ROOT+"/journal/trades/query", json=query, headers=READ)
            assert response.status_code == 200
            page = response.json()
            pages.extend(page["records"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert [v["source"]["record_id"] for v in pages] == [v.record_id for v in captured["session"].records]
        assert c.post(ROOT+"/journal/trades/query", json={"strategy_id": "unknown"}, headers=READ).json()["records"] == []
        research = c.post(ROOT+"/journal/research/query", json={"limit": 1}, headers=READ).json()
        assert research["records"][0]["operation"]["operation_id"] == captured["operation"].operation_id
        assert c.get(ROOT+"/ready", headers=READ).status_code == 200
    assert captured["close_thread"] == captured["thread"] != get_ident()
    # Reopening exercises existing disk verification, not a new API persistence format.
    with SQLiteJournal(path) as journal:
        assert len(journal.trade_history().records) == 4
        assert len(journal.research_history().records) == 1
    assert captured["journal"]._closed


def test_absent_reporting_services_unknown_owners_and_query_bounds():
    with client() as c:
        assert c.get(ROOT+"/paper/sessions/unknown/snapshot", headers=AUTH).status_code == 404
        assert c.get(ROOT+"/portfolios/unknown/snapshot", headers=AUTH).status_code == 404
        assert c.post(ROOT+"/journal/trades/query", json={}, headers=AUTH).status_code == 503
        assert c.post(ROOT+"/journal/research/query", json={}, headers=AUTH).status_code == 503
        assert c.get(ROOT+"/journal/sessions/unknown/summary", headers=AUTH).status_code == 503
        for query in ({"limit": 21}, {"limit": True}, {"start": "2026-01-01"},
            {"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
            {"database_path": "C:/private.db"}):
            assert c.post(ROOT+"/journal/trades/query", json=query, headers=AUTH).status_code == 422


def test_cursor_binding_and_half_open_utc_query():
    captured = {}
    with client(factory=reporting_factory(captured)) as c:
        first = c.post(ROOT+"/journal/trades/query", json={"limit": 1}, headers=AUTH).json()
        cursor = first["next_cursor"]
        response = c.post(ROOT+"/journal/trades/query", json={"session_id": "unknown", "after": cursor}, headers=AUTH)
        assert response.status_code == 422 and response.json()["code"] == "query_rejected"
        timestamp = first["records"][0]["source"]["timestamp"]
        before = c.post(ROOT+"/journal/trades/query", json={"end": timestamp}, headers=AUTH).json()
        assert before["records"] == []
        after = c.post(ROOT+"/journal/trades/query", json={"start": timestamp}, headers=AUTH).json()
        assert len(after["records"]) == 4


def test_actual_durable_recovery_operator_gate_remains_local(tmp_path):
    from tests.persistence.test_paper import create, reopen
    from tests.paper.test_sessions import inputs
    captured = {}
    def factory():
        path = tmp_path/"paper.db"
        owner, store, reference, args, policy = create(path)
        for item in inputs():
            owner.process(item)
        store.close()
        restored, store = reopen(path, reference, args, policy)
        captured.update(owner=restored, store=store)
        return ApplicationServices(paper_sessions={restored.config.strategy.session_id: restored}, close_owners=store.close)
    with client(factory=factory) as c:
        identity = captured["owner"].config.strategy.session_id
        assert captured["owner"].operator_required
        assert c.get(ROOT+f"/paper/sessions/{identity}/snapshot", headers=AUTH).json()["operator_required"]
        assert c.post(ROOT+f"/paper/sessions/{identity}/resume", headers=AUTH).status_code == 404
        assert captured["owner"].operator_required
        c.portal.call(c.app.state.lane.call, lambda: setattr(captured["owner"], "_poisoned", True))
        for path in (ROOT+f"/paper/sessions/{identity}/snapshot", ROOT+"/ready"):
            response = c.get(path, headers=AUTH)
            assert response.status_code == 503 and response.json()["code"] == "recovery_required"


def test_journal_uncertain_commit_is_fail_closed():
    captured = {}
    with client(factory=reporting_factory(captured)) as c:
        c.portal.call(c.app.state.lane.call, lambda: setattr(captured["journal"], "_unusable", True))
        response = c.post(ROOT+"/journal/trades/query", json={}, headers=AUTH)
        assert response.status_code == 503 and response.json()["code"] == "recovery_required"
        assert c.get(ROOT+"/ready", headers=AUTH).status_code == 503
