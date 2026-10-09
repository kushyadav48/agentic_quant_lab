"""Failing-first focused Phase 20 integrity and query-plan regressions."""
from decimal import Decimal as D
import sqlite3

import pytest

from quantlab.journal import (HistoryQuery, JournalError, JournalEvent,
    JournalRecoveryRequired, ResearchLink, SQLiteJournal)
from quantlab.mcp.canonical import canonical_json, digest_json
from quantlab.mcp.models import BacktestExecutionResult
from quantlab.mcp.operations import ResearchOperations
from quantlab.paper.models import stable_id
from quantlab.paper.strategy_models import record
from tests.journal.test_journal import register, entry, evidence_for
from tests.mcp.test_operations import submission
from tests.paper.test_sessions import inputs, T
from tests.persistence.test_advanced import setup


@pytest.mark.parametrize("field,value", [("strategy_id","unrelated"),
    ("strategy_version",99), ("strategy_content_digest","f"*64)])
def test_completed_result_must_match_declared_strategy(field,value):
    operations = ResearchOperations()
    queued = operations.submit(submission())
    done = operations.execute(queued.operation_id)
    result = BacktestExecutionResult.model_validate_json(done.result_json)
    result = result.model_copy(update={"value": result.value.model_copy(update={field:value})})
    wire = canonical_json(result.model_dump(mode="python"))
    digest = digest_json(wire)
    forged = done.model_copy(update={"result_json":wire, "result_digest":digest,
        "audit":(*done.audit[:-1],done.audit[-1].model_copy(update={"result_digest":digest}))})
    with SQLiteJournal() as journal:
        with pytest.raises(ValueError,match="strategy"):
            journal.ingest_research("audit",forged)
        assert journal.events() == ()


def test_evidence_cannot_mix_strategy_identity_across_holdout_segments():
    owner, owners = setup()
    with SQLiteJournal() as journal:
        session = register(journal,owner,owners)
        source = next(e for e in evidence_for(owners,session) if hasattr(e.report,"in_sample"))
        out = source.report.out_of_sample
        changed = out.model_copy(update={"backtest":out.backtest.model_copy(update={"strategy_id":"unrelated"})})
        report = source.report.model_copy(update={"out_of_sample":changed})
        forged = record(type(source), **{**source.model_dump(exclude={"record_id"}),
            "report":report,"result_digest":stable_id("paper-research-result-v1",report)})
        before = journal.events()
        with pytest.raises(ValueError,match="strategy"):
            journal.ingest_research("audit",forged)
        assert journal.events() == before


@pytest.mark.parametrize("field,value", [("strategy_id","unrelated"),
    ("entry_transaction_id","unrelated"), ("entry_time",T),
    ("fees_paid",D("0")), ("realized_pnl",D("42"))])
def test_position_identity_and_outcomes_bind_actual_fill(field,value):
    owner, owners = setup()
    with SQLiteJournal() as journal:
        register(journal,owner,owners)
        for item in inputs()[:-1]: journal.ingest_trade(owner.process(item))
        source = owner.process(inputs()[-1])
        position = source.account.position.model_copy(update={field:value})
        account = source.account.model_copy(update={"position":position})
        forged = record(type(source), **{**source.model_dump(exclude={"record_id"}),"account":account})
        before = journal.events()
        with pytest.raises(JournalError,match="position"):
            journal.ingest_trade(forged)
        assert journal.events() == before
        journal.ingest_trade(source)


def test_rejected_replay_link_does_not_commit_a_replacement_suffix(tmp_path):
    owner, owners = setup()
    with SQLiteJournal() as journal:
        session = register(journal,owner,owners)
        evidence = evidence_for(owners,session)[0]
        research = journal.ingest_research("audit",evidence)
        link = journal.link_research(session.config.strategy.session_id,research.record_id)
        events = journal.events()
    wrong = record(ResearchLink, **{**link.model_dump(exclude={"record_id"}),"admission_id":"f"*64})
    bad = record(JournalEvent, **{**events[-1].model_dump(exclude={"record_id"}),"payload":wrong.canonical_json()})
    path = tmp_path/"replay.db"
    with pytest.raises(JournalError): SQLiteJournal.replay(path,(*events[:-1],bad))
    with SQLiteJournal(path) as recovered:
        assert recovered.events() == events[:-1]
        assert not recovered._connection.execute("SELECT 1 FROM links").fetchone()


def test_rollback_failure_poisoning_prevents_uncommitted_reads(tmp_path,monkeypatch):
    class BrokenRollback:
        def __init__(self, connection): self.connection = connection
        def __getattr__(self,name): return getattr(self.connection,name)
        def rollback(self): raise sqlite3.OperationalError("injected rollback failure")
    path = tmp_path/"rollback.db"
    owner, owners = setup()
    journal = SQLiteJournal(path)
    register(journal,owner,owners)
    source = owner.process(inputs()[0])
    journal._connection = BrokenRollback(journal._connection)
    def fail(stage):
        if stage == "before_commit": raise RuntimeError("injected precommit failure")
    monkeypatch.setattr(journal,"_fault",fail)
    try:
        with pytest.raises(JournalRecoveryRequired): journal.ingest_trade(source)
        with pytest.raises(JournalRecoveryRequired): journal.trade_history()
    finally:
        journal.close()
    with SQLiteJournal(path) as recovered:
        assert recovered.trade_history().records == ()


@pytest.mark.parametrize("kind,filters", [("trade",{"order_id":"f"*64}),
    ("research",{"strategy_id":"absent"}),
    ("trade",{"strategy_version":999}),
    ("trade",{"strategy_digest":"f"*64}),
    ("trade",{"instrument_id":"absent"})])
def test_selective_history_queries_do_not_scan_unrelated_history(kind,filters):
    with SQLiteJournal() as journal:
        statements = []
        journal._connection.set_trace_callback(statements.append)
        getattr(journal,kind+"_history")(HistoryQuery(limit=1,**filters))
        sql = next(s for s in statements if s.startswith("SELECT t.payload"))
        plan = journal._connection.execute("EXPLAIN QUERY PLAN "+sql).fetchall()
        assert not any("SCAN t " in row[3] for row in plan), plan
