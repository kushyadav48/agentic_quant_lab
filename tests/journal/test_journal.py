"""Journal imports use real committed owners, never invented execution economics."""
from decimal import Decimal as D, localcontext
import sqlite3

import pytest
from pydantic import ValidationError
from quantlab.journal import (HistoryQuery, JournalError, JournalIdentityConflict,
    JournalRecoveryRequired, JournalSession, NoteRevision, SQLiteJournal)
from quantlab.paper import FillRecord, OCOFillRecord, OCOQuantityAdjustment
from quantlab.paper.strategy_models import record
from tests.persistence.test_advanced import setup, execute, exit_command, q, reopened
from tests.paper.test_oco import create
from tests.paper.test_sessions import command, inputs, T
from tests.backtesting.helpers import MINUTE
from tests.paper.advanced_entry_helpers import session as advanced_session, prefix, quote, cancel
from tests.mcp.test_operations import submission
from quantlab.mcp.operations import ResearchOperations


def register(journal, owner, owners):
    value = JournalSession(config=owner.config, strategy=owners["strategy"],
        admission=owner.snapshot.runtime.admission)
    assert journal.register_session(value) == value
    return value


def imported(journal, owner, items):
    results = []
    for item in items:
        r = owner.process(item)
        assert journal.ingest_trade(r) == r
        results.append(r)
    return results


def entry(journal, owner):
    return imported(journal, owner, inputs())


def evidence_for(owners, session):
    return tuple(owners["evidence"].get(e.evidence_id) for e in session.admission.evidence)


def paginate(journal, kind="trade", **filters):
    result, cursor = [], None
    while True:
        page = getattr(journal, kind+"_history")(HistoryQuery(limit=2, after=cursor, **filters))
        result.extend(page.records)
        cursor = page.next_cursor
        if cursor is None:
            return tuple(result)


@pytest.mark.parametrize("oco", [False, True])
@pytest.mark.parametrize("stop", [False, True])
def test_partial_exit_fees_realized_pnl_and_oco(oco, stop):
    owner, owners = setup()
    with SQLiteJournal() as j:
        session = register(j, owner, owners)
        records = entry(j, owner)
        after_entry = j.summary(session.config.strategy.session_id)
        assert after_entry.fill_count == 1 and after_entry.lifecycle == "open"
        assert after_entry.net_realized_pnl == D("-1.2")
        assert after_entry.closed_outcome is None
        assert after_entry.account.realized_pnl == 0
        cmd = create(owner) if oco else exit_command(owner, **(
            {"order_type": "stop_market", "stop_price": D("96"), "protective_role": "stop_loss"}
            if stop else {"order_type": "limit", "limit_price": D("105"), "protective_role": "take_profit"}))
        records += imported(j, owner, (cmd,))
        if stop:
            records += imported(j, owner, (q(7, "95"),))
        n = 8 if stop else 7
        partial = imported(j, owner, (q(n, "94" if stop else "110"),))[0]
        before = j.trade_history()
        assert j.summary(session.config.strategy.session_id).lifecycle == "open"
        assert partial.account.realized_pnl == D("-7" if stop else "9")
        assert partial.account.fees_paid == D("2.3")
        records.append(partial)
        records += imported(j, owner, (q(n+1, "93" if stop else "111"),))
        summary = j.summary(session.config.strategy.session_id)
        assert summary.lifecycle == "closed" and summary.fill_count == 3
        assert summary.net_realized_pnl == D("-18.4" if stop else "15.6")
        assert summary.closed_outcome == ("loss" if stop else "profit")
        assert summary.account == owner.snapshot.account
        values = paginate(j)
        assert tuple(v.source for v in values) == tuple(records)
        assert sum((v.realized_pnl_delta for v in values), D(0)) == summary.account.realized_pnl
        assert sum((v.fees_delta for v in values), D(0)) == D("3.4")
        assert sum((v.net_realized_pnl_delta for v in values), D(0)) == summary.net_realized_pnl
        # Entry and exit execution prices remain the real recorded prices.
        fills = [e for v in values for e in v.source.orders if isinstance(e, FillRecord)]
        assert [f.execution.execution_price for f in fills] == [D("101"), D("94" if stop else "110"), D("93" if stop else "111")]
        if oco:
            assert all(isinstance(f, OCOFillRecord) for f in fills[1:])
            assert all(f.group_id == records[-1].oco.group_id for f in fills[1:])
            assert any(isinstance(e, OCOQuantityAdjustment) for v in values for e in v.source.orders)
        assert before.records[-1].source.account.position.quantity == 1
        for value in (values[0], values[0].source, values[0].session.strategy, summary.account):
            with pytest.raises(ValidationError):
                value.schema_version = 9
        j.verify()


@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_entry_and_cancellation_never_classify_unrealized_as_outcome(cancelled):
    owner, owners = advanced_session(kind="limit")
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        imported(j, owner, prefix())
        assert j.summary(s.config.strategy.session_id).lifecycle == "no_execution"
        partial = imported(j, owner, (quote(6, "99"),))[0]
        if cancelled:
            imported(j, owner, (cancel(owner, 7), cancel(owner, 8, "ack_cancel_entry")))
        else:
            imported(j, owner, (quote(7, "98"),))
        summary = j.summary(s.config.strategy.session_id)
        assert summary.lifecycle == "open" and summary.closed_outcome is None
        assert summary.account.realized_pnl == 0
        assert summary.account.position.quantity == (1 if cancelled else 2)
        assert summary.net_realized_pnl == D("-1.1" if cancelled else "-2.2")
        fills = [e for v in j.trade_history().records for e in v.source.orders if isinstance(e, FillRecord)]
        assert fills[0].cumulative_quantity == 1 and fills[0].remaining_quantity == 1
        assert partial.account.position.entry_basis == 99
        j.verify()


def test_unfilled_cancelled_order_and_no_duplicate_accounting():
    owner, owners = advanced_session(kind="limit")
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        sources = imported(j, owner, prefix())
        sources += imported(j, owner, (cancel(owner, 6), cancel(owner, 7, "ack_cancel_entry")))
        expected, events = j.summary(s.config.strategy.session_id), j.events()
        for _ in range(3):
            for source in sources:
                assert j.ingest_trade(source) == source
            assert j.register_session(s) == s
        assert j.events() == events and j.summary(s.config.strategy.session_id) == expected
        assert expected.fill_count == 0 and expected.lifecycle == "no_execution"
        assert expected.net_realized_pnl == 0
        assert any(e.kind == "cancellation" for v in j.trade_history().records for e in v.source.orders)


def test_source_identity_conflicts_missing_chain_and_invalid_inputs():
    owner, owners = setup()
    with SQLiteJournal() as j:
        with pytest.raises(JournalError, match="missing"):
            j.ingest_trade(owner.process(inputs()[0]))
        s = register(j, owner, owners)
        sources = [owner.process(i) for i in inputs()]
        with pytest.raises(JournalError, match="chain"):
            j.ingest_trade(sources[1])
        for source in sources: j.ingest_trade(source)
        old = j.events()
        changed = record(type(sources[-1]), **sources[-1].model_dump(exclude={"record_id", "reason_reference"}), reason_reference="different")
        with pytest.raises(JournalIdentityConflict): j.ingest_trade(changed)
        with pytest.raises(ValidationError):
            j.ingest_trade(sources[-1].model_copy(update={"record_id": "f"*64}))
        wrong = s.model_copy(update={"config": s.config.model_copy(update={"maximum_inputs": 999})})
        with pytest.raises(JournalIdentityConflict): j.register_session(wrong)
        with pytest.raises(ValidationError):
            j.register_session(s.model_copy(update={"schema_version": 2}))
        assert j.events() == old


def test_queries_filters_cursors_and_same_timestamp_ordering():
    from tests.portfolio.helpers import member_owner, entry_inputs
    with SQLiteJournal() as j:
        expected = []
        for name in ("z", "a"):
            member, owner = member_owner(name)
            s = JournalSession(config=member.config, strategy=member.strategy, admission=member.admission)
            j.register_session(s)
            expected += imported(j, owner, entry_inputs())
        values = paginate(j)
        assert [v.source for v in values] == sorted(expected, key=lambda r: (r.timestamp,r.session_id,r.sequence,r.record_id))
        assert all(v.source.session_id == "session:a" for v in paginate(j, session_id="session:a"))
        assert len(paginate(j, account_id="account:a", strategy_id="a", strategy_version=1,
            instrument_id=values[0].session.config.strategy.account.instrument.instrument_id)) == 4
        assert not paginate(j, strategy_id="absent")
        assert not paginate(j, strategy_version=2)
        assert not paginate(j, strategy_digest="a"*64)
        bounds = paginate(j, start=T, end=T+MINUTE)
        assert all(T <= v.source.timestamp < T+MINUTE for v in bounds)
        fill = next(e for v in values for e in v.source.orders if isinstance(e, FillRecord))
        assert all(any(e.order_id == fill.order_id for e in v.source.orders) for v in paginate(j, order_id=fill.order_id))
        page = j.trade_history(HistoryQuery(limit=1))
        with pytest.raises(JournalError, match="different query"):
            j.trade_history(HistoryQuery(strategy_id="a", after=page.next_cursor))
        with pytest.raises(JournalError): j.trade_history(HistoryQuery(run_id="a"*64))
        for raw in ({"limit": 0}, {"limit": 201}, {"start": T, "end": T}, {"start": T.replace(tzinfo=None)}):
            with pytest.raises(ValidationError): HistoryQuery(**raw)


def test_research_evidence_admission_links_and_full_provenance():
    owner, owners = setup()
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        assert j.research_for_session(s.config.strategy.session_id) == ()
        evidence = evidence_for(owners, s)
        values = [j.ingest_research("application", e) for e in evidence]
        linked = j.research_for_session(s.config.strategy.session_id)
        assert {v.evidence.record_id for v in linked} == {e.evidence_id for e in s.admission.evidence}
        for v in values:
            assert v.evidence in evidence
            assert v.evidence.request_digest and v.evidence.datasets
            assert j.ingest_research("application", v.evidence) == v
            link = j.link_research(s.config.strategy.session_id, v.record_id)
            assert j.link_research(s.config.strategy.session_id, v.record_id) == link
        assert len(j.research_for_session(s.config.strategy.session_id)) == len(linked)
        assert len(paginate(j, "research", strategy_id=s.strategy.strategy_id)) == len(values)
        assert j.research_history(HistoryQuery(run_id=values[0].run_id)).records == (values[0],)
        with pytest.raises(JournalError): j.link_research(s.config.strategy.session_id, "f"*64)
        with pytest.raises(JournalError): j.ingest_research("application", {})
        with pytest.raises(ValidationError): j.ingest_research("application", values[0].evidence.model_copy(update={"schema_version": 9}))
        with pytest.raises(JournalError): j.research_history(HistoryQuery(account_id="account"))
        j.verify()


def test_research_operation_revisions_backfill_namespaces_and_conflicts(monkeypatch):
    from quantlab.mcp import tools
    operations = ResearchOperations()
    queued = operations.submit(submission())
    running = []
    original = tools.run_backtest
    def run(raw):
        running.append(operations.get(queued.operation_id))
        return original(raw)
    monkeypatch.setattr(tools, "run_backtest", run)
    done = operations.execute(queued.operation_id)
    with SQLiteJournal() as j:
        qv = j.ingest_research("server:a", queued)
        rv = j.ingest_research("server:a", running[0])
        dv = j.ingest_research("server:a", done)
        assert [v.revision for v in paginate(j, "research", run_id=qv.run_id)] == [1,2,3]
        assert [v.status for v in (qv,rv,dv)] == ["queued","running","completed"]
        assert dv.operation.result_json == done.result_json
        assert j.ingest_research("server:a", queued) == qv
        other = j.ingest_research("server:b", done)
        assert other.run_id != dv.run_id
        assert other.revision == 3  # terminal-only backfill records no invented prior snapshots
        changed_audit = (queued.audit[0].model_copy(update={"timestamp": queued.audit[0].timestamp+MINUTE}),)
        changed = queued.model_copy(update={"audit": changed_audit})
        with pytest.raises(JournalIdentityConflict): j.ingest_research("server:a", changed)
        j.verify()


def test_notes_versioning_immutability_and_restart(tmp_path):
    owner, owners = setup()
    path = tmp_path/"journal.db"
    with SQLiteJournal(path) as j:
        s = register(j, owner, owners)
        source = entry(j, owner)[-1]
        first = record(NoteRevision, note_id="human-note", revision=1, target_kind="trade",
            target_id=source.record_id, author="human", timestamp=T+MINUTE, text="Review the entry.")
        assert j.append_note(first) == first
        second = record(NoteRevision, **{**first.model_dump(exclude={"record_id"}), "revision": 2,
            "previous_record_id": first.record_id, "timestamp": T+2*MINUTE, "text": "Reviewed."})
        assert j.append_note(second) == second
        assert j.append_note(first) == first
        assert j.notes(first.note_id) == (first, second)
        assert j.annotations("trade", source.record_id) == (second,)
        assert j.trade_history().records[-1].source == source
        bad = record(NoteRevision, **{**second.model_dump(exclude={"record_id"}), "text": "Conflict"})
        with pytest.raises(JournalIdentityConflict): j.append_note(bad)
        bad = record(NoteRevision, **{**second.model_dump(exclude={"record_id"}), "revision": 3,
            "previous_record_id": second.record_id, "target_kind": "session", "target_id": s.config.strategy.session_id})
        with pytest.raises(JournalIdentityConflict): j.append_note(bad)
        bad = record(NoteRevision, **{**first.model_dump(exclude={"record_id"}), "note_id": "missing", "target_id": "f"*64})
        with pytest.raises(JournalError, match="missing"): j.append_note(bad)
        events, summary = j.events(), j.summary(s.config.strategy.session_id)
    with SQLiteJournal(path) as j:
        assert j.events() == events and j.summary(s.config.strategy.session_id) == summary
        assert j.notes("human-note") == (first,second)
    with SQLiteJournal.replay(tmp_path/"replica.db", events) as replica:
        assert replica.events() == events
        assert replica.notes("human-note") == (first,second)


@pytest.mark.parametrize("checkpoint", [False, True])
def test_paper_restart_journal_backfill_and_replay_are_consistent(tmp_path, checkpoint):
    paper_path, journal_path = tmp_path/"paper.db", tmp_path/"journal.db"
    owner, store, _, owners, policy = setup(paper_path)
    with SQLiteJournal(journal_path) as j:
        s = register(j, owner, owners)
        entry(j, owner)
        imported(j, owner, (create(owner), q(7,"110")))
        for evidence in evidence_for(owners, s): j.ingest_research("application", evidence)
        owner.checkpoint()
        before = j.trade_history()
    cfg = owner.config
    store.close()
    restored, store = reopened(paper_path, cfg, owners, policy, checkpoint)
    with SQLiteJournal(journal_path) as j:
        for r in restored.snapshot.records: j.ingest_trade(r)
        assert j.trade_history() == before
        imported(j, restored, (command("pause",10,T+4*MINUTE), command("resume",11,T+4*MINUTE), q(12,"111",time=T+5*MINUTE)))
        assert j.summary(s.config.strategy.session_id).account == restored.snapshot.account
        with SQLiteJournal.replay(tmp_path/"replay.db", j.events()) as replica:
            assert replica.trade_history() == j.trade_history()
            assert replica.research_history() == j.research_history()
    store.close()


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_transaction_fault_windows(tmp_path, monkeypatch, stage):
    owner, owners = setup()
    path = tmp_path/"fault.db"
    j = SQLiteJournal(path)
    register(j, owner, owners)
    source = owner.process(inputs()[0])
    with monkeypatch.context() as patch:
        def fail(actual):
            if actual == stage: raise KeyboardInterrupt(stage)
        patch.setattr(j, "_fault", fail)
        with pytest.raises(KeyboardInterrupt if stage == "before_commit" else JournalRecoveryRequired):
            j.ingest_trade(source)
    if stage == "after_commit":
        with pytest.raises(JournalRecoveryRequired): j.trade_history()
    else:
        assert not j.trade_history().records
    j.close()
    with SQLiteJournal(path) as restored:
        assert len(restored.trade_history().records) == (1 if stage == "after_commit" else 0)
        restored.ingest_trade(source)
        assert len(restored.trade_history().records) == 1


@pytest.mark.parametrize("tamper", ["source", "index", "missing_log", "schema"])
def test_restart_fails_closed_on_corruption(tmp_path, tamper):
    owner, owners = setup()
    path = tmp_path/"bad.db"
    with SQLiteJournal(path) as j:
        register(j, owner, owners)
        entry(j, owner)
    with sqlite3.connect(path) as db:
        if tamper == "source": db.execute("UPDATE events SET payload='{}' WHERE ordinal=2")
        elif tamper == "index": db.execute("DELETE FROM order_events")
        elif tamper == "missing_log": db.execute("DELETE FROM events WHERE ordinal=2")
        else: db.execute("PRAGMA user_version=99")
    with pytest.raises(JournalError): SQLiteJournal(path)


def test_bounded_hot_processing_never_exports_or_scans_prefix(monkeypatch):
    owner, owners = setup()
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        imported(j, owner, inputs())
        statements = []
        j._connection.set_trace_callback(statements.append)
        monkeypatch.setattr(j, "events", lambda: pytest.fail("ordinary processing exported full history"))
        monkeypatch.setattr(j, "verify", lambda: pytest.fail("ordinary processing replayed history"))
        for n in range(6,46):
            source = owner.process(q(n,"100"))
            statements.clear()
            j.ingest_trade(source)
            assert len(statements) <= 10
            assert not any("FROM events ORDER BY ordinal" in sql and "LIMIT 1" not in sql for sql in statements)
        plan = j._connection.execute("EXPLAIN QUERY PLAN SELECT payload FROM trades WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
            (s.config.strategy.session_id,)).fetchall()
        assert any("INDEX" in row[3] for row in plan)
        assert len(j.trade_history(HistoryQuery(limit=2)).records) == 2


def test_hostile_decimal_context_preserves_exact_deltas():
    owner, owners = setup()
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        entry(j, owner)
        imported(j, owner, (exit_command(owner), q(7,"110.123456"), q(8,"111.654321")))
        with localcontext() as ctx:
            ctx.prec = 2
            result = j.trade_history()
            summary = j.summary(s.config.strategy.session_id)
        assert summary.net_realized_pnl == D("16.377777")
        assert result.records[-1].realized_pnl_delta == D("10.654321")


def test_consistently_rehashed_forged_realized_pnl_is_rejected():
    from quantlab.paper.account_models import AccountEvent
    from quantlab.paper.models import stable_id
    owner, owners = setup()
    with SQLiteJournal() as j:
        register(j, owner, owners)
        entry(j, owner)
        imported(j, owner, (exit_command(owner),))
        source = owner.process(q(7,"110"))
        e = source.financial[-1]
        body = e.model_dump(exclude={"event_id"})
        for name in ("realized_pnl", "balance", "equity", "available_funds"):
            body[name] += D("10")
        event = AccountEvent(event_id=stable_id("paper-account-event-v1", body), **body)
        values = source.account.model_dump()
        for name in ("realized_pnl", "balance", "equity", "available_funds"):
            values[name] += D("10")
        values["last_event_id"] = event.event_id
        values["running_peak_equity"] = max(values["running_peak_equity"], values["equity"])
        values["position"]["realized_pnl"] += D("10")
        account = type(source.account).model_validate(values)
        forged = record(type(source), **{**source.model_dump(exclude={"record_id"}),
            "financial": (*source.financial[:-1],event), "account": account})
        before = j.events()
        with pytest.raises(JournalError, match="realized"):
            j.ingest_trade(forged)
        assert j.events() == before
        j.ingest_trade(source)


def test_research_historical_prefix_can_be_backfilled_after_terminal():
    operations = ResearchOperations()
    queued = operations.submit(submission())
    done = operations.execute(queued.operation_id)
    with SQLiteJournal() as j:
        terminal = j.ingest_research("application", done)
        earlier = j.ingest_research("application", queued)
        assert j.research_history().records == (earlier, terminal)
        j.verify()


@pytest.mark.parametrize("kind", ["phase18e-v1", "phase18f-v2-before-oco"])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_frozen_paper_journals_are_linked_without_rewriting(tmp_path, kind, checkpoint):
    import json
    from pathlib import Path
    from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy
    from tests.paper.test_sessions import session as legacy_session
    fixture = json.loads((Path(__file__).parents[1]/"persistence"/"fixtures"/(kind+".json")).read_text(encoding="utf-8"))
    store = SQLitePaperStore(tmp_path/"legacy-paper.db")
    for table in ("metadata", "operations", "checkpoints"):
        for row in fixture[table]:
            store._connection.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", row)
    reference, owners = legacy_session() if kind == "phase18e-v1" else setup()
    owner = DurablePaperSession.recover(store, reference.config, storage_policy=StoragePolicy(checkpoint_interval=2),
        use_checkpoint=checkpoint, **owners)
    original = tuple(e.canonical_json() for e,_,_,_ in store.verify())
    with SQLiteJournal(tmp_path/"legacy-journal.db") as j:
        s = register(j, owner, owners)
        for source in owner.snapshot.records: j.ingest_trade(source)
        assert tuple(v.source for v in j.trade_history().records) == owner.snapshot.records
        assert j.summary(s.config.strategy.session_id).account == owner.snapshot.account
        j.verify()
    assert tuple(e.canonical_json() for e,_,_,_ in store.verify()) == original
    store.close()


def test_journal_replay_refuses_duplicate_reordered_and_missing_events(tmp_path):
    owner, owners = setup()
    with SQLiteJournal() as j:
        register(j, owner, owners)
        entry(j, owner)
        events = j.events()
    for n, forged in enumerate((events[:1]*2, events[1:], tuple(reversed(events)))):
        with pytest.raises(JournalError, match="chain"):
            SQLiteJournal.replay(tmp_path/f"bad-{n}.db", forged)


@pytest.mark.parametrize("state", ["cancelled", "failed"])
def test_terminal_research_outcomes_preserved_without_execution_claims(state, monkeypatch):
    operations = ResearchOperations()
    queued = operations.submit(submission())
    if state == "cancelled": source = operations.cancel(queued.operation_id)
    else:
        from quantlab.mcp import tools
        def fail(raw): raise RuntimeError("offline failure")
        monkeypatch.setattr(tools, "run_backtest", fail)
        with pytest.raises(RuntimeError): operations.execute(queued.operation_id)
        source = operations.get(queued.operation_id)
    with SQLiteJournal() as j:
        value = j.ingest_research("application", source)
        assert value.status == state
        assert value.operation.result_json is None
        assert value.operation == source
        j.verify()


def test_portfolio_owner_and_reporting_are_unchanged_by_journal():
    from tests.portfolio.helpers import member_owner, portfolio, enroll, observe, entry_inputs
    member, owner = member_owner("isolated", advanced=True)
    p = portfolio()
    enroll(p, member)
    with SQLiteJournal() as j:
        j.register_session(JournalSession(config=member.config, strategy=member.strategy, admission=member.admission))
        for item in entry_inputs():
            source = owner.process(item)
            observe(p,member,source)
            before, paper = p.snapshot, owner.snapshot
            j.ingest_trade(source)
            j.summary(member.config.strategy.session_id)
            assert p.snapshot == before and owner.snapshot == paper
        assert j.summary(member.config.strategy.session_id).account == p.snapshot.members[0].account


def test_corrupt_physical_index_and_unrelated_database_are_refused(tmp_path):
    path = tmp_path/"bad-index.db"
    with SQLiteJournal(path): pass
    with sqlite3.connect(path) as db: db.execute("DROP INDEX trade_time")
    with pytest.raises(JournalError, match="schema"): SQLiteJournal(path)
    unrelated = tmp_path/"unrelated.db"
    with sqlite3.connect(unrelated) as db: db.execute("CREATE TABLE existing(value)")
    with pytest.raises(JournalError, match="schema"): SQLiteJournal(unrelated)


def test_short_partial_entry_and_exit_use_recorded_ask_prices():
    from tests.backtesting.helpers import approve, strategy
    from quantlab.strategies import Direction
    spec = approve(strategy(no_exit=True, direction=Direction.SHORT))
    owner, owners = advanced_session(kind="market", spec=spec)
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        imported(j,owner,prefix(close=99,price="101"))
        imported(j,owner,(quote(6,"100"),))
        assert j.summary(s.config.strategy.session_id).account.position.entry_basis == D("100.5")
        imported(j,owner,(exit_command(owner,7,timestamp=T+2*MINUTE),
            q(8,"90",time=T+3*MINUTE), q(9,"89",time=T+4*MINUTE)))
        summary = j.summary(s.config.strategy.session_id)
        assert summary.fill_count == 4
        assert summary.account.realized_pnl == 18
        assert summary.account.fees_paid == D("4.4")
        assert summary.net_realized_pnl == D("13.6") and summary.closed_outcome == "profit"
        j.verify()


def test_rejected_order_retains_risk_without_fictional_fill():
    from quantlab.risk import RiskConfig
    owner, owners = advanced_session(kind="market",risk=RiskConfig(max_position_quantity=D("1")))
    with SQLiteJournal() as j:
        s = register(j, owner, owners)
        imported(j,owner,prefix()[:3])
        events = [e for v in j.trade_history().records for e in v.source.orders]
        assert any(e.kind == "risk" and e.decision is not None for e in events)
        assert not any(isinstance(e,FillRecord) for e in events)
        assert j.summary(s.config.strategy.session_id).lifecycle == "no_execution"
        j.verify()


def test_digest_consistent_incompatible_research_result_is_rejected():
    from quantlab.mcp.canonical import digest_json
    operations = ResearchOperations()
    queued = operations.submit(submission())
    done = operations.execute(queued.operation_id)
    digest = digest_json("{}")
    forged = done.model_copy(update={"result_json": "{}", "result_digest": digest,
        "audit": (*done.audit[:-1], done.audit[-1].model_copy(update={"result_digest": digest}))})
    with SQLiteJournal() as j:
        with pytest.raises(ValueError, match="research result"):
            j.ingest_research("application", forged)
        assert not j.events()


@pytest.mark.parametrize("kind,name", [
    ("holdout","run_holdout"), ("walk_forward","run_walk_forward"),
    ("robustness","run_parameter_robustness"), ("ml_dataset","build_ml_dataset"),
    ("ml_training","train_ml_model"), ("ml_prediction","predict_ml_oos"),
    ("ml_prediction_features","ml_predictions_to_features"), ("performance","analyze_performance")])
def test_every_existing_research_result_codec_is_supported(kind,name):
    from tests.mcp.research_helpers import requests
    from quantlab.mcp import tools
    from tests.mcp.helpers import valid_backtest_request
    raw = requests()[name] if kind != "performance" else {
        "result": tools.run_backtest(valid_backtest_request()).value.model_dump(mode="json")}
    operations = ResearchOperations()
    queued = operations.submit(submission(kind=kind,request=raw))
    done = operations.execute(queued.operation_id)
    with SQLiteJournal() as j:
        value = j.ingest_research("application",done)
        assert value.operation == done
        assert j.research_history().records == (value,)
        j.verify()


def test_rehashed_execution_event_cannot_change_owned_kernel_namespace():
    from quantlab.paper.models import stable_id
    owner, owners = setup()
    with SQLiteJournal() as j:
        register(j,owner,owners)
        imported(j,owner,inputs()[:2])
        source = owner.process(inputs()[2])
        event = source.orders[0]
        body = {**event.model_dump(exclude={"event_id"}), "session_id": "unrelated-owner"}
        changed = type(event)(event_id=stable_id("paper-event-v1",body), **body)
        forged = record(type(source), **{**source.model_dump(exclude={"record_id"}),
            "orders": (changed,*source.orders[1:])})
        with pytest.raises(JournalError,match="execution owner"):
            j.ingest_trade(forged)
        j.ingest_trade(source)
