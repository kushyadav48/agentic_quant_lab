"""Local atomic journal imports and notes; paper accounting remains authoritative.

All indexes are rebuildable projections of an append-only content-addressed log.
This store neither shares a transaction nor grants authority over paper/research.
"""
from contextlib import contextmanager
from decimal import localcontext
from pathlib import Path
import sqlite3

from quantlab.paper.accounting import initialize_account
from quantlab.paper.account_models import exact_context
from quantlab.paper.models import FillRecord, KernelConfig, OCOQuantityAdjustment, stable_id
from quantlab.paper.session_models import SessionRecord
from quantlab.paper.strategy_models import ResearchEvidence, record
from quantlab.mcp.operation_models import OperationSnapshot
from quantlab.backtesting import SignalAction, PositionSide
from quantlab.persistence.contracts import PersistenceError, decode

from .errors import JournalError, JournalIdentityConflict, JournalRecoveryRequired
from .models import (HistoryCursor, HistoryQuery, JournalEvent, JournalSession, NoteRevision,
    ResearchHistoryPage, ResearchHistoryRecord, ResearchLink, SessionSummary,
    TradeHistoryPage, TradeHistoryRecord)


SCHEMA = """
CREATE TABLE events (ordinal INTEGER PRIMARY KEY, record_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, account_id TEXT UNIQUE NOT NULL,
    strategy_id TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL,
    instrument_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX session_strategy ON sessions(strategy_id,version,digest);
CREATE TABLE trades (record_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions,
    sequence INTEGER NOT NULL, timestamp TEXT NOT NULL, input_id TEXT NOT NULL,
    previous_id TEXT, payload TEXT NOT NULL, UNIQUE(session_id,sequence), UNIQUE(session_id,input_id));
CREATE INDEX trade_time ON trades(timestamp,session_id,sequence,record_id);
CREATE INDEX trade_session_time ON trades(session_id,timestamp,sequence,record_id);
CREATE TABLE order_events (session_id TEXT NOT NULL, event_id TEXT NOT NULL,
    record_id TEXT NOT NULL REFERENCES trades, order_id TEXT NOT NULL, kind TEXT NOT NULL,
    PRIMARY KEY(session_id,event_id));
CREATE INDEX order_lookup ON order_events(order_id,record_id);
CREATE TABLE research (record_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, revision INTEGER NOT NULL,
    timestamp TEXT NOT NULL, source_id TEXT NOT NULL, source_kind TEXT NOT NULL,
    result_digest TEXT, payload TEXT NOT NULL, UNIQUE(run_id,revision));
CREATE INDEX research_time ON research(timestamp,run_id,revision,record_id);
CREATE INDEX research_run_time ON research(run_id,timestamp,revision,record_id);
CREATE INDEX research_source ON research(source_kind,source_id,result_digest);
CREATE TABLE research_bindings (record_id TEXT NOT NULL REFERENCES research, strategy_id TEXT NOT NULL,
    version INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(record_id,strategy_id,version,digest));
CREATE INDEX research_strategy ON research_bindings(strategy_id,version,digest,record_id);
CREATE TABLE links (session_id TEXT NOT NULL REFERENCES sessions, research_record_id TEXT NOT NULL REFERENCES research,
    payload TEXT NOT NULL, PRIMARY KEY(session_id,research_record_id));
CREATE TABLE notes (note_id TEXT NOT NULL, revision INTEGER NOT NULL, record_id TEXT UNIQUE NOT NULL,
    target_kind TEXT NOT NULL, target_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(note_id,revision));
CREATE INDEX note_target ON notes(target_kind,target_id,note_id,revision);
"""
TABLES = ("sessions", "trades", "order_events", "research", "research_bindings", "links", "notes")


def _time(value):
    return value.isoformat(timespec="microseconds")


def _decode(cls, wire):
    try:
        return decode(cls, wire)
    except PersistenceError as exc:
        raise JournalError("invalid or incompatible journal record") from exc


class SQLiteJournal:
    """Single-thread local handle. Reopen verifies the complete retained log.

    Writes inspect only the new artifact and indexed current heads. No source
    engine is executed, no wall-clock value is generated and no cache publishes
    ahead of SQLite. Callers deliver committed sources explicitly.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self._closed = False
        self._unusable = False
        self._busy = False
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        try:
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            tables = self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 0 and not tables:
                self._connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nPRAGMA user_version=1;\nCOMMIT;")
            elif version != 1 or {t[0] for t in tables} != {*TABLES, "events"}:
                raise JournalError("incompatible journal schema")
            else:
                self.verify()
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def _check(self):
        if self._closed:
            raise JournalError("journal is closed")
        if self._unusable:
            raise JournalRecoveryRequired("close and reopen journal after uncertain commit")

    def _fault(self, stage):
        """Deterministic fault-injection boundary."""

    def _rollback(self):
        try:
            self._connection.rollback()
        except BaseException as exc:
            self._unusable = True
            raise JournalRecoveryRequired("journal rollback requires reopen verification") from exc

    @contextmanager
    def _read(self):
        """Keep multi-query reports/audits on one SQLite read snapshot."""
        self._check()
        nested = self._connection.in_transaction
        if not nested:
            self._connection.execute("BEGIN")
        try:
            yield
        finally:
            if not nested:
                self._rollback()

    @contextmanager
    def _transaction(self):
        self._check()
        if self._busy:
            raise JournalError("reentrant journal operation")
        self._busy = True
        committing = False
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._fault("before_commit")
            committing = True
            self._connection.commit()
            self._fault("after_commit")
        except BaseException as exc:
            if committing:
                self._unusable = True
                raise JournalRecoveryRequired("journal commit requires reopen verification") from exc
            self._rollback()
            if isinstance(exc, sqlite3.Error):
                raise JournalError("journal transaction failed") from exc
            raise
        finally:
            self._busy = False

    def _append(self, kind, value):
        row = self._connection.execute("SELECT ordinal,record_id FROM events ORDER BY ordinal DESC LIMIT 1").fetchone()
        event = record(JournalEvent, ordinal=1 if row is None else row[0]+1,
            previous_record_id=None if row is None else row[1], kind=kind, payload=value.canonical_json())
        self._connection.execute("INSERT INTO events VALUES (?,?,?)",
            (event.ordinal, event.record_id, event.canonical_json()))

    def _session(self, session_id):
        self._check()
        row = self._connection.execute("SELECT payload FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise JournalError("missing registered session")
        return _decode(JournalSession, row[0])

    def _head(self, session_id):
        row = self._connection.execute("SELECT payload FROM trades WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
            (session_id,)).fetchone()
        return None if row is None else _decode(SessionRecord, row[0])

    def register_session(self, session: JournalSession) -> JournalSession:
        session = JournalSession.model_validate(session)
        cfg = session.config.strategy
        wire = session.canonical_json()
        with self._transaction():
            old = self._connection.execute("SELECT payload FROM sessions WHERE session_id=? OR account_id=?",
                (cfg.session_id, cfg.account.account_id)).fetchone()
            if old is not None:
                if old[0] != wire:
                    raise JournalIdentityConflict("session or account identity conflict")
                return session
            self._connection.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (cfg.session_id,
                cfg.account.account_id, cfg.strategy_id, cfg.strategy_version, cfg.strategy_digest,
                cfg.account.instrument.instrument_id, wire))
            self._append("session", session)
        return session

    def ingest_trade(self, source: SessionRecord) -> SessionRecord:
        source = SessionRecord.model_validate(source)
        wire = source.canonical_json()
        with self._transaction():
            session = self._session(source.session_id)
            old = self._connection.execute("SELECT payload FROM trades WHERE session_id=? AND (input_id=? OR record_id=?)",
                (source.session_id, source.input_id, source.record_id)).fetchone()
            if old is not None:
                if old[0] != wire:
                    raise JournalIdentityConflict("source input identity conflict")
                return source
            head = self._head(source.session_id)
            self._validate_trade(session, head, source)
            self._connection.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", (source.record_id,
                source.session_id, source.sequence, _time(source.timestamp), source.input_id,
                source.previous_record_id, wire))
            self._connection.executemany("INSERT INTO order_events VALUES (?,?,?,?,?)",
                ((source.session_id, e.event_id, source.record_id, e.order_id, e.kind) for e in source.orders))
            self._append("trade", source)
        return source

    @staticmethod
    def _validate_trade(session, head, r):
        cfg = session.config.strategy
        account = initialize_account(cfg.account) if head is None else head.account
        if (r.config_digest != stable_id("paper-replay-config-v1", session.config)
                or r.previous_record_id != (None if head is None else head.record_id)
                or r.sequence <= (0 if head is None else head.sequence)
                or r.timestamp < (cfg.timestamp if head is None else head.timestamp)
                or r.account.config != cfg.account or not account.timestamp <= r.account.timestamp <= r.timestamp):
            raise JournalError("source session chain, configuration or chronology mismatch")
        if (r.market_event is None) == (r.command is None):
            raise JournalError("source requires exactly one retained input")
        original = r.market_event if r.market_event is not None else r.command
        input_id = original.event_id if r.market_event is not None else original.command_id
        if ((r.input_id, r.sequence, r.timestamp) != (input_id, original.sequence, original.timestamp)
                or r.input_digest != stable_id("paper-session-input-v1", original)):
            raise JournalError("source input attribution mismatch")
        if r.market_event is not None:
            obs = r.market_event.observation
            market = None if obs is None else obs.bar if hasattr(obs, "bar") else obs.quote
            if (r.market_event.provenance not in session.config.sources
                    or (market is not None and market.instrument_id != cfg.account.instrument.instrument_id)):
                raise JournalError("source market provenance mismatch")
        if r.decision is not None and (
                r.decision.admission_id != session.admission.record_id
                or r.decision.policy_digest != session.admission.policy_digest
                or any(getattr(r.decision, name) != getattr(cfg, name) for name in
                    ("session_id", "strategy_id", "strategy_version", "strategy_digest"))
                or r.decision.account_id != cfg.account.account_id):
            raise JournalError("source decision attribution mismatch")
        events = {e.event_id: e for e in r.orders}
        if len(events) != len(r.orders):
            raise JournalIdentityConflict("duplicate execution event")
        entry_config = (r.entry_order.config if r.entry_order is not None else KernelConfig(
            session_id=cfg.session_id, instrument=cfg.account.instrument,
            flat_equity=cfg.account.starting_capital, running_peak_equity=cfg.account.starting_capital,
            risk=cfg.risk, costs=cfg.costs))
        heads = [(entry_config, None if r.entry_order is None else r.entry_order.order_id)]
        if entry_config.session_id != cfg.session_id:
            raise JournalError("execution owner attribution mismatch")
        if r.exit_order is not None:
            k = r.exit_order
            if (k.submission is None or k.config.session_id != stable_id("paper-position-exit-kernel-v2",
                    (cfg.session_id, k.submission.command_id))):
                raise JournalError("execution owner attribution mismatch")
            heads.append((k.config,k.order_id))
        if r.oco is not None:
            for role, child in (("stop_loss",r.oco.stop),("take_profit",r.oco.target)):
                if (child.config.session_id != stable_id("paper-oco-child-session-v3",(r.oco.group_id,role))
                        or child.config.account_id != cfg.account.account_id or child.config.strategy_id != cfg.strategy_id):
                    raise JournalError("execution owner attribution mismatch")
                heads.append((child.config,child.order_id))
        owners = {}
        for kernel, order_id in heads:
            if kernel.instrument != cfg.account.instrument or kernel.costs != cfg.costs or kernel.risk != cfg.risk:
                raise JournalError("execution owner configuration mismatch")
            owners[kernel.session_id] = (stable_id("paper-config-v1",kernel),order_id)
        for e in r.orders:
            owner = owners.get(e.session_id)
            if owner is None or e.config_digest != owner[0] or owner[1] is not None and e.order_id != owner[1]:
                raise JournalError("execution owner attribution mismatch")
            if isinstance(e,FillRecord) and e.order_id != stable_id("paper-order-v1",(e.config_digest,e.submission)):
                raise JournalError("fill order identity mismatch")
        version, last_id, timestamp = account.state_version, account.last_event_id, account.timestamp
        realized, fees = account.realized_pnl, account.fees_paid
        position = account.position
        quantity = 0 if position is None else position.quantity
        basis = None if position is None else position.entry_basis
        direction = None if position is None else position.direction
        entry_transaction = None if position is None else position.entry_transaction_id
        entry_time = None if position is None else position.entry_time
        settlements = set()
        with localcontext(exact_context()):
            for e in r.orders:
                # Session, kernel and account sequences have distinct namespaces.
                if e.timestamp > r.timestamp:
                    raise JournalError("future execution event")
                if isinstance(e, OCOQuantityAdjustment) and events.get(e.peer_fill.event_id) != e.peer_fill:
                    raise JournalError("OCO withdrawal does not bind the recorded peer fill")
            for e in r.financial:
                if (e.account_id != cfg.account.account_id or e.strategy_id != cfg.strategy_id
                        or e.previous_event_id != last_id or e.before_version != version
                        or not timestamp <= e.timestamp <= r.timestamp):
                    raise JournalError("source financial chain mismatch")
                if e.kind.startswith("settle"):
                    f = events.get(e.causation_id)
                    if (not isinstance(f, FillRecord) or f.order_id != e.transaction_id
                            or e.causation_id in settlements
                            or f.timestamp != e.timestamp
                            or e.fees_paid - fees != f.execution.costs.commission + f.execution.costs.fees):
                        raise JournalError("settlement does not bind a unique recorded fill and fees")
                    fill = f.execution
                    entry = fill.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)
                    if entry:
                        expected = 0
                        new_direction = PositionSide.LONG if fill.action is SignalAction.ENTER_LONG else PositionSide.SHORT
                        if quantity and direction is not new_direction:
                            raise JournalError("entry fill conflicts with recorded position")
                        if entry_transaction is None:
                            entry_transaction, entry_time = f.order_id, f.timestamp
                        basis = ((0 if basis is None else basis*quantity) + fill.execution_price*fill.quantity)/(quantity+fill.quantity)
                        quantity += fill.quantity
                        direction = new_direction
                    else:
                        expected_action = SignalAction.EXIT_LONG if direction is PositionSide.LONG else SignalAction.EXIT_SHORT
                        if basis is None or fill.action is not expected_action or fill.quantity > quantity:
                            raise JournalError("exit fill conflicts with recorded position")
                        expected = (fill.execution_price-basis if direction is PositionSide.LONG else basis-fill.execution_price)*fill.quantity
                        quantity -= fill.quantity
                    if e.realized_pnl-realized != expected:
                        raise JournalError("realized outcome conflicts with recorded fill and position basis")
                    settlements.add(e.causation_id)
                elif e.realized_pnl != realized or e.fees_paid != fees:
                    raise JournalError("nonsettlement changed realized outcomes")
                if (e.balance != cfg.account.starting_capital + e.realized_pnl - e.fees_paid
                        or e.equity != e.balance + e.unrealized_pnl
                        or e.available_funds != e.balance - e.reserved_funds - e.position_collateral):
                    raise JournalError("source financial economics mismatch")
                version, last_id, timestamp = e.after_version, e.event_id, e.timestamp
                realized, fees = e.realized_pnl, e.fees_paid
            if settlements != {e.event_id for e in r.orders if isinstance(e, FillRecord)}:
                raise JournalError("recorded fill lacks authoritative settlement")
            after = r.account.position
            if (quantity != (0 if after is None else after.quantity)
                    or (after is not None and (after.entry_basis != basis or after.direction is not direction))):
                raise JournalError("recorded fills do not reconcile position quantity and basis")
            if (after is None and position is not None or after is not None and (
                    after.strategy_id != cfg.strategy_id or after.entry_transaction_id != entry_transaction
                    or after.entry_time != entry_time
                    or after.realized_pnl != (0 if position is None else position.realized_pnl) + r.account.realized_pnl-account.realized_pnl
                    or after.fees_paid != (0 if position is None else position.fees_paid) + r.account.fees_paid-account.fees_paid)):
                raise JournalError("position attribution or outcomes conflict with recorded fills")
        if not r.financial:
            if r.account != account:
                raise JournalError("account changed without authoritative financial events")
        elif (r.account.state_version != version or r.account.last_event_id != last_id
                or r.account.timestamp != timestamp or r.account.last_input_sequence != r.financial[-1].input_sequence
                or any(getattr(r.account, n) != getattr(r.financial[-1], n) for n in
                    ("balance", "equity", "available_funds", "reserved_funds", "position_collateral",
                     "realized_pnl", "unrealized_pnl", "fees_paid"))):
            raise JournalError("account conflicts with authoritative financial head")

    def ingest_research(self, origin_namespace: str, source: ResearchEvidence | OperationSnapshot) -> ResearchHistoryRecord:
        if type(source) not in (ResearchEvidence, OperationSnapshot):
            raise JournalError("unsupported research source")
        source = type(source).model_validate(source)
        operation = isinstance(source, OperationSnapshot)
        kind, source_id = ("operation", source.operation_id) if operation else ("evidence", source.record_id)
        value = record(ResearchHistoryRecord, origin_namespace=origin_namespace,
            run_id=stable_id("journal-research-run-v1", (origin_namespace, kind, source_id)),
            revision=len(source.audit) if operation else 1,
            timestamp=source.audit[-1].timestamp if operation else source.verified_at,
            operation=source if operation else None, evidence=None if operation else source)
        self._store_research(value)
        return value

    def _store_research(self, value):
        value = ResearchHistoryRecord.model_validate(value)
        with self._transaction():
            old = self._connection.execute("SELECT payload FROM research WHERE run_id=? AND revision=?",
                (value.run_id, value.revision)).fetchone()
            if old is not None:
                if old[0] != value.canonical_json():
                    raise JournalIdentityConflict("research revision identity conflict")
                return
            prior = self._connection.execute("SELECT payload FROM research WHERE run_id=? ORDER BY revision DESC LIMIT 1",
                (value.run_id,)).fetchone()
            if prior is not None:
                before = _decode(ResearchHistoryRecord, prior[0])
                prefix = min(value.revision, before.revision)
                if (value.operation is None or before.operation is None
                        or value.operation.audit[:prefix] != before.operation.audit[:prefix]):
                    raise JournalIdentityConflict("research audit revision conflicts with retained prefix")
            self._connection.execute("INSERT INTO research VALUES (?,?,?,?,?,?,?,?)", (value.record_id,
                value.run_id, value.revision, _time(value.timestamp), value.source_id, value.source_kind,
                value.evidence.result_digest if value.evidence is not None else value.operation.result_digest,
                value.canonical_json()))
            self._connection.executemany("INSERT INTO research_bindings VALUES (?,?,?,?)",
                ((value.record_id, s.strategy_id, s.strategy_version, s.strategy_digest) for s in set(value.strategies)))
            self._append("research", value)

    def link_research(self, session_id: str, research_record_id: str) -> ResearchLink:
        with self._transaction():
            session = self._session(session_id)
            row = self._connection.execute("SELECT payload FROM research WHERE record_id=?", (research_record_id,)).fetchone()
            if row is None:
                raise JournalError("missing research record")
            research = _decode(ResearchHistoryRecord, row[0])
            cfg = session.config.strategy
            if (research.timestamp > cfg.timestamp or research.status not in ("completed", "verified")
                    or not any((s.strategy_id, s.strategy_version, s.strategy_digest) ==
                        (cfg.strategy_id, cfg.strategy_version, cfg.strategy_digest) for s in research.strategies)):
                raise JournalError("research link requires prior compatible recorded outcome")
            value = record(ResearchLink, session_id=session_id, research_record_id=research_record_id,
                admission_id=session.admission.record_id)
            old = self._connection.execute("SELECT payload FROM links WHERE session_id=? AND research_record_id=?",
                (session_id, research_record_id)).fetchone()
            if old is not None:
                if old[0] != value.canonical_json():
                    raise JournalIdentityConflict("research link identity conflict")
                return value
            self._connection.execute("INSERT INTO links VALUES (?,?,?)", (session_id, research_record_id, value.canonical_json()))
            self._append("link", value)
        return value

    def append_note(self, value: NoteRevision) -> NoteRevision:
        value = NoteRevision.model_validate(value)
        wire = value.canonical_json()
        with self._transaction():
            table, field = {"session": ("sessions", "session_id"), "trade": ("trades", "record_id"),
                "research": ("research", "record_id")}[value.target_kind]
            if self._connection.execute(f"SELECT 1 FROM {table} WHERE {field}=?", (value.target_id,)).fetchone() is None:
                raise JournalError("missing note target")
            old = self._connection.execute("SELECT payload FROM notes WHERE note_id=? AND revision=?",
                (value.note_id, value.revision)).fetchone()
            if old is not None:
                if old[0] != wire:
                    raise JournalIdentityConflict("note revision identity conflict")
                return value
            prior = self._connection.execute("SELECT payload FROM notes WHERE note_id=? ORDER BY revision DESC LIMIT 1",
                (value.note_id,)).fetchone()
            before = None if prior is None else _decode(NoteRevision, prior[0])
            if (value.revision != (1 if before is None else before.revision+1)
                    or value.previous_record_id != (None if before is None else before.record_id)
                    or (before is not None and (value.timestamp < before.timestamp
                        or (value.target_kind, value.target_id) != (before.target_kind, before.target_id)))):
                raise JournalIdentityConflict("note revision chain or target mismatch")
            self._connection.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)", (value.note_id, value.revision,
                value.record_id, value.target_kind, value.target_id, wire))
            self._append("note", value)
        return value

    def notes(self, note_id: str) -> tuple[NoteRevision, ...]:
        self._check()
        return tuple(_decode(NoteRevision, r[0]) for r in self._connection.execute(
            "SELECT payload FROM notes WHERE note_id=? ORDER BY revision", (note_id,)))

    def annotations(self, target_kind: str, target_id: str, *, limit: int = 200) -> tuple[NoteRevision, ...]:
        """Latest immutable revisions, ordered by note identity, with a hard page bound."""
        self._check()
        if target_kind not in ("session", "trade", "research") or type(limit) is not int or not 1 <= limit <= 200:
            raise JournalError("invalid annotation query")
        rows = self._connection.execute("""SELECT n.payload FROM notes n
            WHERE n.target_kind=? AND n.target_id=? AND n.revision=
                (SELECT max(p.revision) FROM notes p WHERE p.note_id=n.note_id)
            ORDER BY n.note_id LIMIT ?""", (target_kind,target_id,limit))
        return tuple(_decode(NoteRevision, r[0]) for r in rows)

    @staticmethod
    def _scope(kind, query):
        return stable_id("journal-query-v1", (kind, query.model_dump(exclude={"after", "limit"})))

    def _query(self, kind, query):
        self._check()
        query = HistoryQuery.model_validate(query)
        scope = self._scope(kind, query)
        if query.after is not None and query.after.scope_digest != scope:
            raise JournalError("cursor belongs to a different query")
        conditions, args = [], []
        owner, sequence = ("t.session_id", "t.sequence") if kind == "trade" else ("t.run_id", "t.revision")
        if kind == "trade":
            if query.run_id is not None:
                raise JournalError("trade query does not support run_id; use research links")
            select = "SELECT t.payload,s.payload,p.payload FROM trades t JOIN sessions s USING(session_id) LEFT JOIN trades p ON p.record_id=t.previous_id"
            for field, column in (("session_id", "t.session_id"), ("account_id", "s.account_id"),
                    ("strategy_id", "s.strategy_id")):
                value = getattr(query, field)
                if value is not None:
                    conditions.append(column+"=?"); args.append(value)
            binding, values = [], []
            for field, column in (("strategy_version", "version"),
                    ("strategy_digest", "digest"), ("instrument_id", "instrument_id")):
                value = getattr(query, field)
                if value is not None:
                    binding.append("m."+column+"=?"); values.append(value)
            if binding:
                conditions.append("t.session_id IN (SELECT m.session_id FROM sessions m WHERE "+" AND ".join(binding)+")")
                args.extend(values)
            if query.order_id is not None:
                conditions.append("t.record_id IN (SELECT o.record_id FROM order_events o WHERE o.order_id=?)")
                args.append(query.order_id)
        else:
            if any(getattr(query, n) is not None for n in ("session_id", "account_id", "instrument_id", "order_id")):
                raise JournalError("research query contains unsupported execution filters")
            select = "SELECT t.payload FROM research t"
            if query.run_id is not None:
                conditions.append("t.run_id=?"); args.append(query.run_id)
            binding, values = [], []
            for field, column in (("strategy_id", "strategy_id"), ("strategy_version", "version"), ("strategy_digest", "digest")):
                if getattr(query, field) is not None:
                    binding.append("b."+column+"=?"); values.append(getattr(query, field))
            if binding:
                conditions.append("t.record_id IN (SELECT b.record_id FROM research_bindings b WHERE "+" AND ".join(binding)+")")
                args.extend(values)
        for value, comparison in ((query.start, ">="), (query.end, "<")):
            if value is not None:
                conditions.append("t.timestamp"+comparison+"?"); args.append(_time(value))
        if query.after is not None:
            cursor = query.after
            conditions.append(f"(t.timestamp,{owner},{sequence},t.record_id)>(?,?,?,?)")
            args.extend((_time(cursor.timestamp), cursor.owner_id, cursor.sequence, cursor.record_id))
        sql = select + (" WHERE "+" AND ".join(conditions) if conditions else "")
        sql += f" ORDER BY t.timestamp,{owner},{sequence},t.record_id LIMIT ?"
        args.append(query.limit+1)
        rows = self._connection.execute(sql, args).fetchall()
        more = len(rows) > query.limit
        rows = rows[:query.limit]
        items = []
        for row in rows:
            if kind == "trade":
                source, session = _decode(SessionRecord, row[0]), _decode(JournalSession, row[1])
                before = initialize_account(session.config.strategy.account) if row[2] is None else _decode(SessionRecord, row[2]).account
                with localcontext(exact_context()):
                    gross, fees = source.account.realized_pnl-before.realized_pnl, source.account.fees_paid-before.fees_paid
                    items.append(TradeHistoryRecord(session=session, source=source,
                        realized_pnl_delta=gross, fees_delta=fees, net_realized_pnl_delta=gross-fees))
            else:
                items.append(_decode(ResearchHistoryRecord, row[0]))
        cursor = None
        if more:
            last = items[-1].source if kind == "trade" else items[-1]
            cursor = HistoryCursor(scope_digest=scope, timestamp=last.timestamp,
                owner_id=last.session_id if kind == "trade" else last.run_id,
                sequence=last.sequence if kind == "trade" else last.revision, record_id=last.record_id)
        return tuple(items), cursor

    def trade_history(self, query: HistoryQuery = HistoryQuery()) -> TradeHistoryPage:
        items, cursor = self._query("trade", query)
        return TradeHistoryPage(records=items, next_cursor=cursor)

    def research_history(self, query: HistoryQuery = HistoryQuery()) -> ResearchHistoryPage:
        items, cursor = self._query("research", query)
        return ResearchHistoryPage(records=items, next_cursor=cursor)

    def research_for_session(self, session_id: str) -> tuple[ResearchHistoryRecord, ...]:
        """Bounded admission references plus explicit links; missing artifacts stay missing."""
        with self._read():
            return self._research_for_session(session_id)

    def _research_for_session(self, session_id):
        session = self._session(session_id)
        ids = {r[0] for r in self._connection.execute("SELECT research_record_id FROM links WHERE session_id=?", (session_id,))}
        for reference in session.admission.evidence:
            ids.update(r[0] for r in self._connection.execute(
                "SELECT record_id FROM research WHERE source_kind='evidence' AND source_id=? AND result_digest=?",
                (reference.evidence_id, reference.result_digest)))
        values = [_decode(ResearchHistoryRecord, self._connection.execute(
            "SELECT payload FROM research WHERE record_id=?", (identity,)).fetchone()[0]) for identity in ids]
        return tuple(sorted(values, key=lambda r: (r.timestamp, r.run_id, r.revision, r.record_id)))

    def summary(self, session_id: str) -> SessionSummary:
        with self._read():
            return self._summary(session_id)

    def _summary(self, session_id):
        session = self._session(session_id)
        head = self._head(session_id)
        account = initialize_account(session.config.strategy.account) if head is None else head.account
        count = self._connection.execute("SELECT count(*) FROM trades WHERE session_id=?", (session_id,)).fetchone()[0]
        fills = self._connection.execute("SELECT count(*) FROM order_events WHERE session_id=? AND kind IN ('fill','advanced_fill','oco_fill_v3')",
            (session_id,)).fetchone()[0]
        lifecycle = "no_execution" if fills == 0 else "open" if account.position is not None and account.position.quantity > 0 else "closed"
        net = account.net_realized_pnl
        outcome = None if lifecycle != "closed" else "profit" if net > 0 else "loss" if net < 0 else "breakeven"
        return SessionSummary(session_id=session_id, source_record_id=None if head is None else head.record_id,
            account=account, record_count=count, fill_count=fills, net_realized_pnl=net,
            lifecycle=lifecycle, closed_outcome=outcome)

    def events(self) -> tuple[JournalEvent, ...]:
        """Explicit full export for backup/replay, never part of ordinary ingestion."""
        self._check()
        return tuple(_decode(JournalEvent, r[0]) for r in self._connection.execute("SELECT payload FROM events ORDER BY ordinal"))

    def _replay_event(self, event):
        head = self._connection.execute("SELECT ordinal,record_id FROM events ORDER BY ordinal DESC LIMIT 1").fetchone()
        if (event.ordinal != (1 if head is None else head[0]+1)
                or event.previous_record_id != (None if head is None else head[1])):
            raise JournalError("journal replay chain mismatch")
        cls = {"session": JournalSession, "trade": SessionRecord, "research": ResearchHistoryRecord,
            "link": ResearchLink, "note": NoteRevision}[event.kind]
        value = _decode(cls, event.payload)
        if event.kind == "session": self.register_session(value)
        elif event.kind == "trade": self.ingest_trade(value)
        elif event.kind == "research": self._store_research(value)
        elif event.kind == "note": self.append_note(value)
        else:
            expected = record(ResearchLink, session_id=value.session_id,
                research_record_id=value.research_record_id,
                admission_id=self._session(value.session_id).admission.record_id)
            if expected != value:
                raise JournalError("research link replay mismatch")
            if self.link_research(value.session_id, value.research_record_id) != value:
                raise JournalError("research link replay mismatch")
        row = self._connection.execute("SELECT payload FROM events ORDER BY ordinal DESC LIMIT 1").fetchone()
        if row is None or _decode(JournalEvent, row[0]) != event:
            raise JournalError("journal replay chain mismatch")

    def verify(self):
        """O(history) startup/explicit audit verifies log and every derived index."""
        with self._read():
            self._verify()

    def _verify(self):
        self._check()
        if self._connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise JournalError("SQLite integrity check failed")
        with SQLiteJournal() as rebuilt:
            schema_sql = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
            if self._connection.execute(schema_sql).fetchall() != rebuilt._connection.execute(schema_sql).fetchall():
                raise JournalError("incompatible journal schema or indexes")
            for ordinal, identity, wire in self._connection.execute("SELECT ordinal,record_id,payload FROM events ORDER BY ordinal"):
                event = _decode(JournalEvent, wire)
                if (event.ordinal, event.record_id) != (ordinal, identity):
                    raise JournalError("journal log identity mismatch")
                rebuilt._replay_event(event)
            for table in TABLES:
                expected = rebuilt._connection.execute(f"SELECT * FROM {table} ORDER BY 1,2")
                actual = self._connection.execute(f"SELECT * FROM {table} ORDER BY 1,2")
                while True:
                    a, b = actual.fetchmany(128), expected.fetchmany(128)
                    if a != b:
                        raise JournalError("journal index/source integrity mismatch")
                    if not a:
                        break

    @classmethod
    def replay(cls, path, events):
        """Import a caller-retained ordered export into a fresh empty journal."""
        owner = cls(path)
        try:
            if owner._connection.execute("SELECT 1 FROM events LIMIT 1").fetchone():
                raise JournalError("replay requires an empty destination")
            for event in events:
                owner._replay_event(JournalEvent.model_validate(event))
            return owner
        except BaseException:
            owner.close()
            raise

    def close(self):
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *args):
        self.close()
