"""Independent audit regressions for Phase 18E recovery boundaries."""
import inspect
import subprocess
from decimal import Decimal as D
import sys

import pytest

from quantlab.paper import SessionState as S, stable_id, PaperIdentityConflict, PaperInputError
from quantlab.paper.session_models import SessionRecord
from quantlab.paper.strategy_models import record
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, RecoveryRequired, PersistenceError
from quantlab.persistence.contracts import Checkpoint, JournalEntry
from quantlab.persistence.store import entry_digest, checkpoint_digest
from tests.persistence.test_paper import create, reopen, rewrite
from tests.paper.test_sessions import inputs, command, control, event, session, T
from tests.paper.strategy_helpers import delivery
from tests.backtesting.helpers import MINUTE


def test_commit_return_interruption_never_exposes_stale_memory(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 4)
    for item in inputs()[:3]:
        owner.process(item)
        reference.process(item)
    lines, first = inspect.getsourcelines(SQLitePaperStore._append)
    target = first + next(i for i, line in enumerate(lines) if line.strip() == "return entry")
    def interrupt(frame, event, arg):
        if frame.f_code is SQLitePaperStore._append.__code__ and event == "line" and frame.f_lineno == target:
            raise KeyboardInterrupt("post-commit return interruption")
        return interrupt
    sys.settrace(interrupt)
    try:
        with pytest.raises((KeyboardInterrupt, RecoveryRequired)):
            owner.process(inputs()[3])
    finally:
        sys.settrace(None)
    try:
        assert owner.recovery_required
        with pytest.raises(RecoveryRequired):
            _ = owner.snapshot
        with pytest.raises(RecoveryRequired):
            _ = owner.records
        with pytest.raises(RecoveryRequired):
            owner.process(inputs()[3])
        assert len(store.verify()) == 4
    finally:
        store.close()
    reference.process(inputs()[3])
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored.snapshot == reference.snapshot
        before = restored.snapshot
        assert restored.process(inputs()[3]) == before.records[-1]
        assert restored.snapshot == before
        assert len(before.execution.openings) == 1
        assert not before.account.reservations
        assert len(store.verify()) == 4
    finally:
        store.close()


def test_historical_pause_retry_keeps_recovered_active_operator_gate(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 3)
    history = (command("start", 1), command("pause", 2), command("resume", 3))
    for item in history:
        owner.process(item)
    before = owner.snapshot
    store.close()
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored.operator_required
        assert restored.process(history[1]) == before.records[1]
        assert restored.operator_required
        assert restored.snapshot.state is S.ACTIVE
        with pytest.raises(RecoveryRequired, match="operator_pause_required"):
            restored.process(control("heartbeat", 4, T))
    finally:
        store.close()


def rehash_last(path, entry, outcome, effects, checkpoint):
    values = entry.model_dump()
    values.update(output=outcome.canonical_json(), effects=effects.canonical_json(),
                  output_digest=stable_id("paper-durable-output-v1", (outcome, effects)))
    changed = JournalEntry(**values)
    values["digest"] = entry_digest(changed)
    changed = JournalEntry(**values)
    rewrite(path, "UPDATE operations SET entry=?,digest=? WHERE ordinal=?",
            (changed.canonical_json(), changed.digest, changed.ordinal))
    rewrite(path, "UPDATE metadata SET head=?", (changed.digest,))
    values = checkpoint.model_dump()
    values.update(head_digest=changed.digest, record_id=outcome.record_id,
                  state=outcome.state, feed=outcome.feed, account=outcome.account,
                  financial_count=checkpoint.financial_count + len(outcome.financial) - len(SessionRecord.model_validate_json(entry.output).financial),
                  kernel=effects.kernel, intent=effects.intent)
    cp = Checkpoint(**values)
    values["digest"] = checkpoint_digest(cp)
    cp = Checkpoint(**values)
    rewrite(path, "UPDATE checkpoints SET payload=?,digest=? WHERE ordinal=?",
            (cp.canonical_json(), cp.digest, cp.ordinal))


def test_checkpoint_rejects_hash_consistent_invalid_opening_reason(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 4)
    owner.replay(inputs(), maximum_events=4)
    entry, item, outcome, effects = store.verify()[-1]
    checkpoint = store.checkpoints()[-1]
    store.close()
    values = outcome.model_dump(exclude={"record_id"})
    values["reason"] = "observed"
    outcome = record(SessionRecord, **values)
    rehash_last(path, entry, outcome, effects, checkpoint)
    for use_checkpoint in (False, True):
        with pytest.raises(PersistenceError):
            reopen(path, reference, args, policy, use_checkpoint=use_checkpoint)


def financial_kwargs():
    from quantlab.backtesting import BacktestConfig, ExecutionCostConfig
    return dict(research_config=BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(slippage=D("0.5"), commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1"))))


def final_operation(kind):
    return inputs()[3] if kind == "fill" else command("stop", 6, T + MINUTE)


def assert_closed(owner, item):
    assert owner.recovery_required
    with pytest.raises(RecoveryRequired):
        _ = owner.snapshot
    with pytest.raises(RecoveryRequired):
        _ = owner.records
    with pytest.raises(RecoveryRequired):
        owner.process(item)
    with pytest.raises(RecoveryRequired):
        owner.checkpoint()


@pytest.mark.parametrize("kind", ["fill", "release"])
@pytest.mark.parametrize("stage,committed", [
    ("before_commit", False), ("commit_armed", False),
    ("commit_before", False), ("commit_after", True),
    ("commit_line", False), ("after_commit_line", True),
    ("append_handoff", True), ("owner_return", True),
    ("before_publication", True), ("after_publication", True),
])
def test_keyboard_interrupt_financial_boundaries(tmp_path, monkeypatch, kind, stage, committed):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 4, **financial_kwargs())
    for item in inputs()[:3]:
        owner.process(item)
        reference.process(item)
    previous = owner.snapshot
    item = final_operation(kind)
    actual = store._connection
    if stage in ("before_commit", "commit_armed"):
        def fault(where):
            if where == stage:
                raise KeyboardInterrupt(stage)
        monkeypatch.setattr(store, "_fault", fault)
    elif stage in ("commit_before", "commit_after"):
        class Connection:
            def __getattr__(self, name):
                return getattr(actual, name)
            def commit(self):
                if committed:
                    actual.commit()
                raise KeyboardInterrupt(stage)
        monkeypatch.setattr(store, "_connection", Connection())
    elif stage == "append_handoff":
        original = store._append
        def append(*a, **kw):
            original(*a, **kw)
            raise KeyboardInterrupt(stage)
        monkeypatch.setattr(store, "_append", append)
    elif stage in ("before_publication", "after_publication"):
        original = owner._publish_candidate
        def publish(candidate):
            if stage == "after_publication":
                original(candidate)
            raise KeyboardInterrupt(stage)
        monkeypatch.setattr(owner, "_publish_candidate", publish)
    else:
        lines, first = inspect.getsourcelines(SQLitePaperStore._append)
        marker = "c.commit()" if stage == "commit_line" else "self._transaction_state = _TransactionState.COMMITTED"
        target = first + next(i for i, line in enumerate(lines) if line.strip() == marker)
        def interrupt(frame, event, arg):
            if stage == "owner_return":
                hit = frame.f_code is DurablePaperSession._commit_candidate.__code__ and event == "return"
            else:
                hit = frame.f_code is SQLitePaperStore._append.__code__ and event == "line" and frame.f_lineno == target
            if hit:
                raise KeyboardInterrupt(stage)
            return interrupt
        sys.settrace(interrupt)
    try:
        with pytest.raises((KeyboardInterrupt, RecoveryRequired)):
            owner.process(item)
    finally:
        sys.settrace(None)
    try:
        assert len(store.verify()) == (4 if committed else 3)
        if stage == "before_commit":
            assert not owner.recovery_required
            assert owner.snapshot == previous
            monkeypatch.undo()
            assert owner.process(item) == reference.process(item)
            committed = True
        else:
            assert_closed(owner, item)
    finally:
        store.close()
    if committed and stage != "before_commit":
        reference.process(item)
    for use_checkpoint in (False, True):
        mode_path = tmp_path / f"recover-{use_checkpoint}.db"
        mode_path.write_bytes(path.read_bytes())
        restored, store = reopen(mode_path, reference, args, policy, use_checkpoint=use_checkpoint)
        try:
            assert restored.snapshot == reference.snapshot
            if committed:
                before = restored.snapshot
                restored.process(item)
                assert restored.snapshot == before
                assert not before.account.reservations
                fills = [e for e in before.execution.kernel.events if e.kind == "fill"]
                releases = [e for e in before.records[-1].financial if e.kind == "release"]
                assert len(fills) == int(kind == "fill")
                assert len(releases) == int(kind == "release")
                assert len(before.execution.openings) == int(kind == "fill")
                assert before.account.fees_paid == (D("1.2") if kind == "fill" else D("0"))
            else:
                restored.pause(command("pause", 10, T + MINUTE))
                restored.resume(command("resume", 11, T + MINUTE))
                reference_copy, _ = session(**financial_kwargs())
                reference_copy.replay(inputs()[:3], maximum_events=3)
                reference_copy.pause(command("pause", 10, T + MINUTE))
                reference_copy.resume(command("resume", 11, T + MINUTE))
                # A release is legal after reconciliation; the original opening
                # is earlier than the explicit operator clock and cannot be backdated.
                if kind == "release":
                    retry = command("stop", 12, T + MINUTE)
                    assert restored.process(retry) == reference_copy.process(retry)
                    assert restored.snapshot == reference_copy.snapshot
                else:
                    with pytest.raises(PaperInputError):
                        restored.process(item)
        finally:
            store.close()


def test_interrupted_rollback_blocks_reads_and_retry(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    owner.start(command("start"))
    reference.start(command("start"))
    actual = store._connection
    class Connection:
        def __getattr__(self, name):
            return getattr(actual, name)
        def rollback(self):
            raise KeyboardInterrupt("rollback interrupted")
    monkeypatch.setattr(store, "_connection", Connection())
    def fault(stage):
        if stage == "before_commit":
            raise RuntimeError("prepare failed")
    monkeypatch.setattr(store, "_fault", fault)
    with pytest.raises(RecoveryRequired, match="rollback_outcome_unknown"):
        owner.process(inputs()[1])
    assert_closed(owner, inputs()[1])
    store.close()
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored.snapshot == reference.snapshot
    finally:
        store.close()


@pytest.mark.parametrize("use_checkpoint", [False, True])
@pytest.mark.parametrize("retry_kind", ["start", "pause", "resume", "paused_event", "bar", "quote", "opening"])
def test_all_historical_retries_preserve_operator_gate(tmp_path, use_checkpoint, retry_kind):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 1)
    if retry_kind in ("bar", "quote", "opening"):
        history = (*inputs(), command("pause", 6, T + MINUTE), command("resume", 7, T + MINUTE))
        index = {"bar": 1, "quote": 2, "opening": 3}[retry_kind]
    else:
        history = (command("start", 1), command("pause", 2), control("heartbeat", 3, T), command("resume", 4))
        index = {"start": 0, "pause": 1, "paused_event": 2, "resume": 3}[retry_kind]
    for item in history:
        owner.process(item)
    before = owner.snapshot
    store.close()
    restored, store = reopen(path, reference, args, policy, use_checkpoint=use_checkpoint)
    try:
        item = history[index]
        assert restored.process(item) == before.records[index]
        assert restored.operator_required
        assert restored.snapshot == before
        changed = item.model_copy(update={"timestamp": item.timestamp + MINUTE})
        with pytest.raises(PaperIdentityConflict):
            restored.process(changed)
        assert restored.operator_required
        assert restored.snapshot == before
        with pytest.raises(RecoveryRequired, match="operator_pause_required"):
            restored.process(control("heartbeat", 10, T + MINUTE))
        assert len(store.verify()) == len(history)
        restored.pause(command("pause", 9, T + MINUTE))
        assert not restored.operator_required and restored.snapshot.state is S.PAUSED
        inactive = restored.process(control("heartbeat", 10, T + MINUTE))
        assert inactive.reason == "inactive"
        restored.resume(command("resume", 11, T + MINUTE))
        expected_reason = "observed" if retry_kind in ("bar", "quote", "opening") else "missing"
        assert restored.process(control("heartbeat", 12, T + MINUTE)).reason == expected_reason
    finally:
        store.close()


@pytest.mark.parametrize("use_checkpoint", [False, True])
@pytest.mark.parametrize("action", ["pause", "stop", "fail"])
def test_historical_active_results_cannot_reactivate_terminal_or_paused_owner(tmp_path, use_checkpoint, action):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 1)
    owner.start(command("start"))
    owner.process(command(action, 2))
    before = owner.snapshot
    store.close()
    restored, store = reopen(path, reference, args, policy, use_checkpoint=use_checkpoint)
    try:
        assert restored.process(command("start")) == before.records[0]
        assert restored.snapshot == before
        if action == "pause":
            assert restored.process(control("heartbeat", 3, T)).reason == "inactive"
        else:
            with pytest.raises(PaperInputError):
                restored.resume(command("resume", 3))
            assert restored.snapshot == before
    finally:
        store.close()


@pytest.mark.parametrize("what", ["missing_opening", "orders", "release", "cancel_effects", "bar_reason", "quote_reason", "decision", "reason_reference", "opening_causality"])
def test_hash_consistent_semantic_corruption_rejected_by_both_recovery_modes(tmp_path, what):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    history = list(inputs())
    if what in ("release", "cancel_effects"):
        history = [*inputs()[:3], command("stop", 6, T + MINUTE)]
    elif what == "bar_reason":
        history = list(inputs()[:2])
    elif what == "quote_reason":
        history = list(inputs()[:3])
    elif what == "decision":
        history.append(event(delivery(1, close=99, sequence=6)))
    elif what == "reason_reference":
        history = [command("start")]
    owner.replay(history, maximum_events=len(history))
    checkpoint = owner.checkpoint()
    retained = store.verify()
    entry, item, out, effects = retained[-1]
    store.close()
    values = out.model_dump(exclude={"record_id"})
    if what in ("missing_opening", "cancel_effects"):
        previous_out, previous_effects = retained[-2][2:]
        values.update(orders=(), financial=(), account=previous_out.account)
        effects = previous_effects.model_copy(update={"financial_inputs": ()})
    elif what == "orders":
        values["orders"] = ()
    elif what == "release":
        values.update(financial=(), account=retained[-2][2].account)
        effects = effects.model_copy(update={"financial_inputs": ()})
    elif what in ("bar_reason", "quote_reason"):
        values["reason"] = "observed"
    elif what == "decision":
        d = out.decision.model_dump(exclude={"record_id"})
        d["dependencies"] = ("invented:dependency", *out.decision.dependencies)
        from quantlab.paper.strategy_models import StrategyDecision
        values["decision"] = record(StrategyDecision, **d)
    elif what == "reason_reference":
        values["reason_reference"] = "human:unrecorded"
    else:
        item = item.model_copy(update={"observation": item.observation.model_copy(update={"previous_close_id": "other:bar"})})
        values.update(market_event=item, input_digest=stable_id("paper-session-input-v1", item))
        entry = entry.model_copy(update={"payload": item.canonical_json()})
    out = record(SessionRecord, **values)
    rehash_last(path, entry, out, effects, checkpoint)
    original_bytes = path.read_bytes()
    for use_checkpoint in (False, True):
        with pytest.raises(PersistenceError):
            reopen(path, reference, args, policy, use_checkpoint=use_checkpoint)
        assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("kind", ["fill", "release"])
@pytest.mark.parametrize("stage,committed", [("commit_armed", False), ("after_commit", True), ("store_return", True)])
def test_process_termination_financial_handoff(tmp_path, kind, stage, committed):
    path = tmp_path / "crash.db"
    code = f"""
import inspect, os, sys
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy
from tests.paper.test_sessions import session, inputs
from tests.persistence.test_recovery_defects import financial_kwargs, final_operation
reference,args=session(**financial_kwargs())
store=SQLitePaperStore({str(path)!r})
owner=DurablePaperSession(store,reference.config,storage_policy=StoragePolicy(checkpoint_interval=4),**args)
for item in inputs()[:3]: owner.process(item)
def crash(stage):
    if stage == {stage!r}: os._exit(29)
store._fault=crash
if {stage!r} == 'store_return':
    lines,first=inspect.getsourcelines(SQLitePaperStore._append)
    target=first+next(i for i,line in enumerate(lines) if line.strip()=='return entry')
    def interrupt(frame,event,arg):
        if frame.f_code is SQLitePaperStore._append.__code__ and event=='line' and frame.f_lineno==target: os._exit(29)
        return interrupt
    sys.settrace(interrupt)
owner.process(final_operation({kind!r}))
raise AssertionError('termination not reached')
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=40)
    assert result.returncode == 29, result.stdout + result.stderr
    from tests.paper.test_sessions import session
    reference, args = session(**financial_kwargs())
    reference.replay(inputs()[:3], maximum_events=3)
    item = final_operation(kind)
    if committed:
        reference.process(item)
    from quantlab.persistence import StoragePolicy
    for use_checkpoint in (False, True):
        restored, store = reopen(path, reference, args, StoragePolicy(checkpoint_interval=4), use_checkpoint=use_checkpoint)
        try:
            assert restored.snapshot == reference.snapshot
            assert len(store.verify()) == (4 if committed else 3)
            if committed:
                before = restored.snapshot
                restored.process(item)
                assert restored.snapshot == before
                assert not before.account.reservations
                assert len(before.execution.openings) == int(kind == "fill")
                assert before.account.fees_paid == (D("1.2") if kind == "fill" else D("0"))
        finally:
            store.close()


def test_decoder_keeps_strict_checks_with_and_without_duration_adaptation():
    from quantlab.persistence.contracts import decode, JournalEntry
    from quantlab.paper.session_models import ReplayConfig
    from tests.paper.test_sessions import session
    reference, _ = session()
    assert decode(ReplayConfig, reference.config.canonical_json()) == reference.config
    item = command("start")
    assert decode(type(item), item.canonical_json()) == item
    body = item.canonical_json()
    for invalid in (body[:-1] + ',"sequence":1}', body.replace('"sequence":1', '"sequence":1.0'), body + ' '):
        with pytest.raises(PersistenceError, match="invalid_record"):
            decode(type(item), invalid)


@pytest.mark.parametrize("stage", ["commit_armed", "after_commit"])
def test_shared_commit_state_blocks_reads_before_owner_exception_handler(tmp_path, monkeypatch, stage):
    owner, store, *_ = create(tmp_path / "paper.db")
    seen = []
    def fault(actual):
        if actual == stage:
            assert not owner._poisoned
            assert owner.recovery_required
            with pytest.raises(RecoveryRequired):
                _ = owner.snapshot
            with pytest.raises(RecoveryRequired):
                _ = owner.records
            seen.append(actual)
    monkeypatch.setattr(store, "_fault", fault)
    try:
        owner.start(command("start"))
        assert seen == [stage]
        assert not owner.recovery_required
        assert owner.snapshot.state is S.ACTIVE
    finally:
        store.close()


def test_manual_checkpoint_return_interruption_retains_uncertainty(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    for item in inputs():
        owner.process(item)
        reference.process(item)
    lines, first = inspect.getsourcelines(SQLitePaperStore._save_checkpoint)
    target = first + max(i for i, line in enumerate(lines) if line.strip() == "return cp")
    def interrupt(frame, event, arg):
        if frame.f_code is SQLitePaperStore._save_checkpoint.__code__ and event == "line" and frame.f_lineno == target:
            raise KeyboardInterrupt("checkpoint return interruption")
        return interrupt
    sys.settrace(interrupt)
    try:
        with pytest.raises((KeyboardInterrupt, RecoveryRequired)):
            owner.checkpoint()
    finally:
        sys.settrace(None)
    try:
        assert_closed(owner, inputs()[3])
    finally:
        store.close()
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored.snapshot == reference.snapshot
    finally:
        store.close()


def test_config_digest_reuse_keeps_binding_and_record_verification(tmp_path, monkeypatch):
    from quantlab.persistence import store as store_module
    calls = []
    original = store_module.stable_id
    def measured(namespace, value):
        if namespace == "paper-replay-config-v1":
            calls.append(value)
        return original(namespace, value)
    monkeypatch.setattr(store_module, "stable_id", measured)
    owner, store, *_ = create(tmp_path / "paper.db")
    try:
        owner.replay(inputs(), maximum_events=4)
        assert len(store.verify()) == 4
        assert len(calls) == 1
        store._connection.execute("UPDATE operations SET digest=? WHERE ordinal=4", ("f" * 64,))
        with pytest.raises(PersistenceError, match="index_mismatch"):
            store.verify()
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["fill", "release"])
def test_sqlite_vm_interruption_during_real_commit_requires_recovery(tmp_path, kind):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 4, **financial_kwargs())
    for item in inputs()[:3]:
        owner.process(item)
        reference.process(item)
    actual = store._connection
    armed, hits = False, []
    def trace(sql):
        nonlocal armed
        if sql.strip().upper() == "COMMIT":
            armed = True
    def interrupt():
        nonlocal armed
        if armed:
            armed = False  # One interruption; allow the cleanup rollback.
            hits.append("COMMIT")
            return 1
        return 0
    actual.set_trace_callback(trace)
    actual.set_progress_handler(interrupt, 1)
    item = final_operation(kind)
    try:
        with pytest.raises(RecoveryRequired, match="commit_outcome_unknown"):
            owner.process(item)
    finally:
        actual.set_trace_callback(None)
        actual.set_progress_handler(None, 0)
    try:
        assert hits == ["COMMIT"]
        assert_closed(owner, item)
        assert len(store.verify()) == 3
    finally:
        store.close()
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored.snapshot == reference.snapshot
        t = restored.snapshot.clock.timestamp
        restored.pause(command("pause", 6, t))
        restored.resume(command("resume", 7, t))
        restored.stop(command("stop", 8, t))
        assert not restored.snapshot.account.reservations
        assert restored.snapshot.account.fees_paid == D("0")
        assert not restored.snapshot.execution.openings
        assert not [e for e in restored.snapshot.execution.kernel.events if e.kind == "fill"]
        assert len([e for e in restored.records[-1].financial if e.kind == "release"]) == 1
    finally:
        store.close()
