"""Phase 18E real-file, restart, fault-window and financial regressions."""
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from pydantic import ValidationError

from quantlab.paper import PaperIdentityConflict, PaperInputError, SessionState as S, stable_id
from quantlab.persistence import (DurablePaperSession, SQLitePaperStore, StoragePolicy,
                                 PersistenceError, RecoveryRequired)
from quantlab.persistence.contracts import Checkpoint, Effects, JournalEntry, decode
from quantlab.persistence.store import checkpoint_digest, entry_digest
from tests.paper.test_sessions import session, inputs, command, control, event, T
from tests.paper.strategy_helpers import delivery, close_quote, opening
from tests.backtesting.helpers import MINUTE



def create(path, interval=0, **kwargs):
    reference, args = session(**kwargs)
    policy = StoragePolicy(checkpoint_interval=interval)
    store = SQLitePaperStore(path)
    owner = DurablePaperSession(store, reference.config, storage_policy=policy, **args)
    return owner, store, reference, args, policy


def reopen(path, reference, args, policy, **kwargs):
    store = SQLitePaperStore(path)
    try:
        owner = DurablePaperSession.recover(store, reference.config, storage_policy=policy, **args, **kwargs)
    except BaseException:
        store.close()
        raise
    return owner, store


def rewrite(path, sql, values=()):
    with sqlite3.connect(path) as c:
        c.execute(sql, values)


@pytest.mark.parametrize("prefix", range(5))
@pytest.mark.parametrize("interval", [0, 2, 3])
def test_new_owner_exact_recovery_and_retained_retry(tmp_path, prefix, interval):
    path = tmp_path / "session.db"
    owner, store, reference, args, policy = create(path, interval)
    items = inputs()[:prefix]
    for item in items:
        assert owner.process(item) == reference.process(item)
    before = owner.snapshot
    store.close()
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored is not owner
        assert restored.snapshot == before == reference.snapshot
        assert restored.operator_required == (before.state is S.ACTIVE)
        checkpoint = prefix // interval * interval if interval else 0
        assert restored.recovery_checkpoint_ordinal == checkpoint
        assert restored.recovery_replayed_inputs == prefix - checkpoint
        for item, expected in zip(items, before.records):
            assert restored.process(item) == expected
            assert restored.snapshot == before
        if prefix:
            item = items[-1]
            changed = item.model_copy(update={"timestamp": item.timestamp + MINUTE})
            with pytest.raises(PaperIdentityConflict):
                restored.process(changed)
            assert restored.snapshot == before
        retained = store.verify()
        assert len(retained) == prefix
        for entry, item, out, effects in retained:
            assert decode(JournalEntry, entry.canonical_json()) == entry
            assert entry.digest == entry_digest(entry)
            assert decode(type(item), entry.payload) == item
            assert decode(Effects, entry.effects) == effects
    finally:
        store.close()


@pytest.mark.parametrize("interval", [0, 2, 4])
@pytest.mark.parametrize("lifecycle", ["pause", "stop", "interruption", "exhaustion", "capacity"])
def test_lifecycle_pending_reservations_survive_or_cancel(tmp_path, interval, lifecycle):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, interval, maximum_inputs=3 if lifecycle == "capacity" else 100)
    owner.replay(inputs()[:3], maximum_events=3)
    reference.replay(inputs()[:3], maximum_events=3)
    item = command("stop" if lifecycle == "capacity" else lifecycle, 6, T + MINUTE) if lifecycle in ("pause", "stop", "capacity") else control(lifecycle, 6, T + MINUTE)
    assert owner.process(item) == reference.process(item)
    before = owner.snapshot
    store.close()
    restored, store = reopen(path, reference, args, policy)
    try:
        assert restored.snapshot == before
        assert restored.snapshot.account.position is None
        assert restored.snapshot.account.fees_paid == 0
        if lifecycle in ("stop", "capacity"):
            assert restored.snapshot.state is S.STOPPED
            assert not restored.snapshot.account.reservations
            assert restored.snapshot.execution.kernel.state.value == "cancelled"
            with pytest.raises(PaperInputError):
                restored.resume(command("resume", 7, T + MINUTE))
        else:
            assert len(restored.snapshot.account.reservations) == 1
            restored.stop(command("stop", 7, T + MINUTE))
            assert not restored.snapshot.account.reservations
    finally:
        store.close()


@pytest.mark.parametrize("prefix", [1, 3, 4])
def test_recovered_active_requires_recorded_pause_resume(tmp_path, prefix):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 2)
    for item in inputs()[:prefix]:
        owner.process(item)
    before = owner.snapshot
    store.close()
    owner, store = reopen(path, reference, args, policy)
    try:
        heartbeat = control("heartbeat", 10, T + MINUTE)
        with pytest.raises(RecoveryRequired, match="operator_pause_required"):
            owner.process(heartbeat)
        assert owner.snapshot == before
        owner.pause(command("pause", 6, T + MINUTE))
        assert not owner.operator_required
        owner.resume(command("resume", 7, T + MINUTE))
        owner.process(heartbeat)
        assert owner.snapshot.feed.accepted == before.feed.accepted + 1
    finally:
        store.close()


@pytest.mark.parametrize("stage", ["before_prepare", "serialization", "before_commit"])
@pytest.mark.parametrize("prefix", [1, 2, 3])
def test_precommit_failure_preserves_authority_and_safe_retry(tmp_path, monkeypatch, stage, prefix):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 2)
    for item in inputs()[:prefix]:
        owner.process(item)
    item = inputs()[prefix]
    before = owner.snapshot
    def fault(actual):
        if actual == stage:
            raise RuntimeError("injected precommit failure")
    monkeypatch.setattr(store, "_fault", fault)
    with pytest.raises(RuntimeError):
        owner.process(item)
    assert owner.snapshot == before and not owner.recovery_required
    assert len(store.verify()) == prefix
    assert store.lookup(item.event_id) is None
    monkeypatch.setattr(store, "_fault", lambda stage: None)
    owner.process(item)
    after = owner.snapshot
    assert owner.process(item) == after.records[-1]
    assert owner.snapshot == after
    store.close()
    restored, store = reopen(path, reference, args, policy)
    assert restored.snapshot == after
    store.close()


@pytest.mark.parametrize("target", ["record", "indexes", "effects", "checkpoint"])
def test_failure_before_serialized_transaction(tmp_path, monkeypatch, target):
    import quantlab.persistence.paper as module
    owner, store, reference, args, policy = create(tmp_path / "paper.db", 1)
    before = owner.snapshot
    def fail(*args, **kwargs):
        raise ValueError("injected allocation or serialization failure")
    with monkeypatch.context() as patch:
        if target == "record":
            patch.setattr(owner, "_prepare_record", fail)
        elif target == "indexes":
            original = owner._stage_record
            def stage(*args):
                original(*args)
                fail()
            patch.setattr(owner, "_stage_record", stage)
        else:
            patch.setattr(module, "_effects" if target == "effects" else "_checkpoint", fail)
        with pytest.raises(ValueError):
            owner.start(command("start"))
    assert owner.snapshot == before and store.verify() == []
    owner.start(command("start"))
    store.close()


def test_database_constraint_violation_rolls_back_all_records(tmp_path, monkeypatch):
    owner, store, *_ = create(tmp_path / "paper.db", 1)
    before = owner.snapshot
    def fault(stage):
        if stage == "before_commit":
            store._connection.execute("INSERT INTO operations SELECT * FROM operations")
    monkeypatch.setattr(store, "_fault", fault)
    with pytest.raises(PersistenceError, match="transaction_failed"):
        owner.start(command("start"))
    assert owner.snapshot == before
    assert store.verify() == [] and store.checkpoints() == []
    monkeypatch.setattr(store, "_fault", lambda stage: None)
    owner.start(command("start"))
    store.close()


@pytest.mark.parametrize("stage", ["after_commit", "before_publication", "after_publication"])
@pytest.mark.parametrize("prefix", [1, 2, 3])
def test_committed_unpublished_requires_new_owner_recovery(tmp_path, monkeypatch, stage, prefix):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 2)
    for item in inputs()[:prefix]:
        owner.process(item)
        reference.process(item)
    item = inputs()[prefix]
    if stage == "after_commit":
        def fault(actual):
            if actual == stage:
                raise RuntimeError("injected crash window")
        monkeypatch.setattr(store, "_fault", fault)
    else:
        original = owner._publish_candidate
        def publish(candidate):
            if stage == "after_publication":
                original(candidate)
            raise RuntimeError("injected publication failure")
        monkeypatch.setattr(owner, "_publish_candidate", publish)
    with pytest.raises(RecoveryRequired):
        owner.process(item)
    assert owner.recovery_required
    with pytest.raises(RecoveryRequired):
        _ = owner.snapshot
    with pytest.raises(RecoveryRequired):
        _ = owner.records
    with pytest.raises(RecoveryRequired):
        owner.process(item)
    assert len(store.verify()) == prefix + 1
    store.close()
    reference.process(item)
    restored, store = reopen(path, reference, args, policy)
    assert restored.snapshot == reference.snapshot
    assert restored.process(item) == reference.records[-1]
    assert len(store.verify()) == prefix + 1
    store.close()


@pytest.mark.parametrize("stage,committed", [("before_commit", 3), ("after_commit", 4)])
def test_controlled_process_exit_at_financial_commit(tmp_path, stage, committed):
    path = tmp_path / "crash.db"
    code = f"""
import os
from quantlab.persistence import *
from tests.paper.test_sessions import session, inputs
reference, args = session()
store = SQLitePaperStore({str(path)!r})
owner = DurablePaperSession(store, reference.config, storage_policy=StoragePolicy(checkpoint_interval=2), **args)
for item in inputs()[:3]: owner.process(item)
def crash(stage):
    if stage == {stage!r}: os._exit(23)
store._fault = crash
owner.process(inputs()[3])
raise AssertionError('did not terminate')
"""
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=40)
    assert child.returncode == 23, child.stdout + child.stderr
    reference, args = session()
    reference.replay(inputs()[:committed], maximum_events=committed)
    restored, store = reopen(path, reference, args, StoragePolicy(checkpoint_interval=2))
    assert restored.snapshot == reference.snapshot
    assert len(store.verify()) == committed
    if committed == 4:
        before = restored.snapshot
        restored.process(inputs()[3])
        assert restored.snapshot == before
        assert len(before.execution.openings) == 1
        assert len([e for e in before.execution.kernel.events if e.kind == "fill"]) == 1
        assert not before.account.reservations
    store.close()


@pytest.mark.parametrize("what", ["first", "middle", "last", "payload", "output", "effects", "digest", "chain", "sequence", "identity", "type", "version", "timestamp", "truncate", "duplicate_keys", "reorder", "tail", "metadata"])
def test_corrupted_journal_refuses_recovery(tmp_path, what):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    owner.replay(inputs(), maximum_events=4)
    store.close()
    if what in ("first", "middle", "last"):
        rewrite(path, "DELETE FROM operations WHERE ordinal=?", ({"first": 1, "middle": 2, "last": 4}[what],))
    elif what == "tail":
        rewrite(path, "UPDATE metadata SET count=3")
    elif what == "metadata":
        rewrite(path, "DELETE FROM metadata")
    elif what == "reorder":
        rewrite(path, "UPDATE operations SET entry=(SELECT entry FROM operations WHERE ordinal=3) WHERE ordinal=2")
    else:
        with sqlite3.connect(path) as c:
            wire = c.execute("SELECT entry FROM operations WHERE ordinal=4").fetchone()[0]
        body = json.loads(wire)
        if what in ("payload", "output", "effects"):
            body[what] = "{}"
        elif what == "digest":
            body["digest"] = "f" * 64
        elif what == "chain":
            body["previous_digest"] = "f" * 64
        elif what == "sequence":
            body["logical_sequence"] = 3
        elif what == "identity":
            body["input_id"] = "different"
        elif what == "type":
            body["input_type"] = "broker_order"
        elif what == "version":
            body["schema_version"] = 99
        elif what == "timestamp":
            body["timestamp"] = "2020-01-01T00:00:00"
        new = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if what == "truncate":
            new = wire[:len(wire) // 2]
        if what == "duplicate_keys":
            new = wire[:-1] + ',"ordinal":4}'
        rewrite(path, "UPDATE operations SET entry=? WHERE ordinal=4", (new,))
    with pytest.raises(PersistenceError):
        reopen(path, reference, args, policy)


@pytest.mark.parametrize("what", ["digest", "reference", "state", "version", "malformed"])
@pytest.mark.parametrize("use_checkpoint", [True, False])
def test_invalid_checkpoint_never_silently_ignored(tmp_path, what, use_checkpoint):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 2)
    owner.replay(inputs(), maximum_events=4)
    store.close()
    with sqlite3.connect(path) as c:
        wire = c.execute("SELECT payload FROM checkpoints WHERE ordinal=4").fetchone()[0]
    body = json.loads(wire)
    if what == "digest":
        body["digest"] = "f" * 64
    elif what == "reference":
        body["head_digest"] = "f" * 64
    elif what == "state":
        body["state"] = "stopped"
    elif what == "version":
        body["schema_version"] = 2
    new = json.dumps(body, sort_keys=True, separators=(",", ":")) if what != "malformed" else "{"
    if what in ("reference", "state"):
        # Rehash to test semantic reference validation, beyond the outer digest.
        cp = decode(Checkpoint, new)
        body["digest"] = checkpoint_digest(cp)
        new = json.dumps(body, sort_keys=True, separators=(",", ":"))
    rewrite(path, "UPDATE checkpoints SET payload=?,digest=? WHERE ordinal=4", (new, body["digest"]))
    with pytest.raises(PersistenceError):
        reopen(path, reference, args, policy, use_checkpoint=use_checkpoint)


@pytest.mark.parametrize("change", ["config", "strategy", "policy", "eligibility", "storage"])
def test_changed_bindings_refused(tmp_path, change):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    owner.start(command("start"))
    store.close()
    args = dict(args)
    config = reference.config
    if change == "config":
        config = config.model_copy(update={"maximum_inputs": 99})
    elif change == "strategy":
        args["strategy"] = args["strategy"].model_copy(update={"approval": args["strategy"].approval.model_copy(update={"reviewer": "another:human"})})
    elif change == "policy":
        args["policy"] = args["policy"].model_copy(update={"rationale_reference": "other:policy"})
    elif change == "eligibility":
        args["eligibility"] = args["eligibility"].model_copy(update={"reason_reference": "other:review"})
    else:
        policy = StoragePolicy(checkpoint_interval=1)
    with SQLitePaperStore(path) as store:
        with pytest.raises((PersistenceError, PaperInputError, ValidationError)):
            DurablePaperSession.recover(store, config, storage_policy=policy, **args)


@pytest.mark.parametrize("version", [2, 99])
def test_unknown_sqlite_version_refused(tmp_path, version):
    path = tmp_path / "paper.db"
    store = SQLitePaperStore(path)
    store.close()
    rewrite(path, f"PRAGMA user_version={version}")
    with pytest.raises(PersistenceError, match="schema_mismatch"):
        SQLitePaperStore(path)


@pytest.mark.parametrize("invalid", [":memory:", "file:paper.db", "", None, 123])
def test_invalid_storage_paths(invalid):
    with pytest.raises(PersistenceError, match="invalid_path"):
        SQLitePaperStore(invalid)


def test_missing_parent_directory_and_non_database(tmp_path):
    with pytest.raises(PersistenceError):
        SQLitePaperStore(tmp_path / "absent" / "paper.db")
    with pytest.raises(PersistenceError):
        SQLitePaperStore(tmp_path)
    path = tmp_path / "bad.db"
    path.write_bytes(b"truncated not a database")
    with pytest.raises(PersistenceError):
        SQLitePaperStore(path)
    path = tmp_path / "other.db"
    rewrite(path, "CREATE TABLE other (value TEXT)")
    with pytest.raises(PersistenceError, match="schema_mismatch"):
        SQLitePaperStore(path)


def test_one_writer_one_owner_explicit_configuration_and_lifecycle(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    c = store._connection
    assert c.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert c.execute("PRAGMA synchronous").fetchone() == (3,)
    assert c.execute("PRAGMA locking_mode").fetchone() == ("exclusive",)
    assert c.execute("PRAGMA foreign_keys").fetchone() == (1,)
    with pytest.raises(PersistenceError):
        SQLitePaperStore(path)
    with pytest.raises(PersistenceError, match="store_already_owned"):
        DurablePaperSession(store, reference.config, **args)
    store.close()
    store.close()
    with pytest.raises(PersistenceError, match="store_closed"):
        owner.process(command("start"))
    with SQLitePaperStore(path) as other:
        with pytest.raises(PersistenceError, match="session_already_exists"):
            DurablePaperSession(other, reference.config, **args)


@pytest.mark.parametrize("interval", [0, 1, 3])
def test_bounded_checkpoints_and_optional_full_replay(tmp_path, interval):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, interval)
    owner.replay(inputs(), maximum_events=4)
    for seq in range(6, 16):
        owner.process(control("heartbeat", seq, T + MINUTE))
    expected = owner.snapshot
    cps = store.checkpoints()
    assert len(cps) <= policy.retained_checkpoints
    if cps:
        assert len(cps[-1].canonical_json()) < 20_000
    store.close()
    restored, store = reopen(path, reference, args, policy, use_checkpoint=False)
    assert restored.snapshot == expected
    assert restored.recovery_replayed_inputs == 14
    store.close()


def test_distinct_equal_quotes_are_distinct_committed_inputs(tmp_path):
    owner, store, *_ = create(tmp_path / "paper.db")
    owner.start(command("start"))
    q = close_quote(delivery(close=99), sequence=2)
    a = event(q)
    b = event(q.model_copy(update={"event_id": "other-source-event", "sequence": 3}))
    owner.process(a)
    owner.process(b)
    assert owner.snapshot.feed.accepted == 2
    owner.process(a)
    assert len(store.verify()) == 3
    with pytest.raises(PaperIdentityConflict):
        owner.process(a.model_copy(update={"sequence": -1}))
    assert len(store.verify()) == 3
    store.close()


def test_independent_stores_session_and_account_isolation(tmp_path):
    a, sa, reference, args, policy = create(tmp_path / "a.db", 2)
    config = reference.config.model_copy(update={"strategy": reference.config.strategy.model_copy(update={
        "session_id": "session-b", "account": reference.config.strategy.account.model_copy(update={"account_id": "account-b"})})})
    with SQLitePaperStore(tmp_path / "b.db") as sb:
        b = DurablePaperSession(sb, config, storage_policy=policy, **args)
        a.replay(inputs(), maximum_events=4)
        b.start(command("start"))
        assert a.snapshot.account.position is not None
        assert b.snapshot.account.position is None
        assert len(sb.verify()) == 1
        assert a.records[-1].session_id != b.records[-1].session_id
    sa.close()


def test_no_network_or_background_execution(tmp_path, monkeypatch):
    import asyncio
    import socket
    import threading
    import urllib.request
    def denied(*args, **kwargs):
        raise AssertionError("external work forbidden")
    with monkeypatch.context() as patch:
        for obj, name in [(socket, "socket"), (socket, "create_connection"),
                          (threading.Thread, "start"), (asyncio, "create_task"), (urllib.request, "urlopen")]:
            patch.setattr(obj, name, denied)
        owner, store, reference, args, policy = create(tmp_path / "paper.db", 2)
        owner.replay(inputs(), maximum_events=4)
        expected = owner.snapshot
        store.close()
        restored, store = reopen(tmp_path / "paper.db", reference, args, policy)
        assert restored.snapshot == expected
        store.close()


@pytest.mark.parametrize("committed", [True, False])
def test_exception_during_commit_is_ambiguous_and_never_reports_success(tmp_path, committed):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    actual = store._connection
    class Connection:
        def __getattr__(self, name):
            return getattr(actual, name)
        def commit(self):
            if committed:
                actual.commit()
            raise sqlite3.OperationalError("injected unknown commit outcome")
    store._connection = Connection()
    with pytest.raises(RecoveryRequired, match="commit_outcome_unknown"):
        owner.start(command("start"))
    assert owner.recovery_required
    with pytest.raises(RecoveryRequired):
        owner.start(command("start"))
    store.close()
    restored, store = reopen(path, reference, args, policy)
    if committed:
        reference.start(command("start"))
    assert restored.snapshot == reference.snapshot
    assert len(store.verify()) == int(committed)
    store.close()


@pytest.mark.parametrize("interval", [0, 2])
@pytest.mark.parametrize("short", [False, True])
def test_nonzero_fees_slippage_positions_and_opening_provenance(tmp_path, interval, short):
    from decimal import Decimal as D
    from quantlab.backtesting import BacktestConfig, ExecutionCostConfig
    from quantlab.strategies import Direction
    from tests.backtesting.helpers import strategy
    cfg = BacktestConfig(initial_capital=D("1000"), quantity=D("2"), execution_costs=ExecutionCostConfig(
        slippage=D("0.5"), commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1")))
    path = tmp_path / "paper.db"
    spec = strategy(direction=Direction.SHORT if short else Direction.LONG, no_exit=True)
    owner, store, reference, args, policy = create(path, interval, spec=spec, research_config=cfg)
    items = inputs(close=99 if short else 101)
    for item in items:
        assert owner.process(item) == reference.process(item)
    before = owner.snapshot
    assert before.account.fees_paid == D("1.2")
    assert before.account.balance == D("998.8")
    assert len(before.execution.openings) == 1
    assert before.account.reservations == ()
    store.close()
    restored, store = reopen(path, reference, args, policy)
    assert restored.snapshot == before
    assert restored.snapshot.account.position.direction.value == ("short" if short else "long")
    for item in items:
        restored.process(item)
    assert restored.snapshot == before
    restored.stop(command("stop", 6, T + MINUTE))
    assert restored.snapshot.account == before.account
    store.close()


@pytest.mark.parametrize("offset", [0, 1, 3, 100])
def test_checkpoint_rebuilds_causal_feature_offset_indexes(tmp_path, offset):
    from quantlab.strategies import FeatureOperand, FeatureReference, FeatureType
    from tests.backtesting.helpers import group, rule, strategy
    f = FeatureReference(feature_id="close", implementation_id="close", feature_type=FeatureType.INDICATOR)
    spec = strategy(no_exit=True, features=(f,), entry=group(rule(left=FeatureOperand(feature_id="close", offset=offset))))
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path, 3, spec=spec)
    generated = [command("start")]
    generated += [event(delivery(i, close=99, sequence=i + 2)) for i in range(5)]
    for item in generated:
        assert owner.process(item) == reference.process(item)
    before = owner.snapshot
    store.close()
    restored, store = reopen(path, reference, args, policy)
    assert restored.snapshot == before
    assert restored.recovery_checkpoint_ordinal == 6
    restored.pause(command("pause", 7, before.clock.timestamp))
    restored.resume(command("resume", 8, before.clock.timestamp))
    reference.pause(command("pause", 7, before.clock.timestamp))
    reference.resume(command("resume", 8, before.clock.timestamp))
    next_bar = event(delivery(5, close=99, sequence=9))
    assert restored.process(next_bar) == reference.process(next_bar)
    assert restored.snapshot == reference.snapshot
    store.close()


@pytest.mark.parametrize("value", [True, -1, 100001, "1", 1.5])
def test_strict_checkpoint_policy(value):
    with pytest.raises(ValidationError):
        StoragePolicy(checkpoint_interval=value)


@pytest.mark.parametrize("field,value", [("sequence", True), ("sequence", "1"),
    ("schema_version", 2), ("timestamp", "2026-01-01T00:00:00"), ("action", "trade"), ("extra", "field")])
def test_strict_noncanonical_disk_input(field, value):
    body = json.loads(command("start").canonical_json())
    body[field] = value
    with pytest.raises(PersistenceError, match="invalid_record"):
        decode(type(command("start")), json.dumps(body, sort_keys=True, separators=(",", ":")))


def test_manual_checkpoint_is_idempotent_accelerator(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    with pytest.raises(PaperInputError):
        owner.checkpoint()
    owner.replay(inputs(), maximum_events=4)
    before = owner.snapshot
    cp = owner.checkpoint()
    assert owner.checkpoint() == cp
    assert owner.snapshot == before and len(store.verify()) == 4
    assert len(store.checkpoints()) == 1
    store.close()
    restored, store = reopen(path, reference, args, policy)
    assert restored.snapshot == before and restored.recovery_checkpoint_ordinal == 4
    store.close()


def test_manual_checkpoint_transaction_failure_preserves_history(tmp_path, monkeypatch):
    owner, store, *_ = create(tmp_path / "paper.db")
    owner.replay(inputs(), maximum_events=4)
    before = owner.snapshot
    def fail(stage):
        if stage == "before_checkpoint_commit":
            raise RuntimeError("checkpoint fault")
    monkeypatch.setattr(store, "_fault", fail)
    with pytest.raises(RuntimeError):
        owner.checkpoint()
    assert owner.snapshot == before
    assert len(store.verify()) == 4 and store.checkpoints() == []
    store.close()


def test_replay_mismatch_is_diagnostic_and_refuses_activation(tmp_path):
    from quantlab.paper.strategy_models import record
    from quantlab.paper.session_models import SessionRecord
    from quantlab.paper.models import canonical_json
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    owner.replay(inputs(), maximum_events=4)
    entries = store.verify()
    store.close()
    e, item, out, effects = entries[-1]
    values = out.model_dump(exclude={"record_id"})
    values["reason"] = "observed"
    out = record(SessionRecord, **values)
    values = e.model_dump()
    values.update(output=out.canonical_json(), output_digest=stable_id("paper-durable-output-v1", (out, effects)))
    candidate = JournalEntry(**values)
    values["digest"] = entry_digest(candidate)
    candidate = JournalEntry(**values)
    rewrite(path, "UPDATE operations SET entry=?,digest=? WHERE ordinal=4", (candidate.canonical_json(), candidate.digest))
    rewrite(path, "UPDATE metadata SET head=?", (candidate.digest,))
    with SQLitePaperStore(path) as store:
        with pytest.raises(PersistenceError, match="replay_mismatch"):
            DurablePaperSession.recover(store, reference.config, storage_policy=policy, **args)
        assert store.last_recovery_failure == "replay_mismatch"


def test_schema_tampering_refused(tmp_path):
    path = tmp_path / "paper.db"
    with SQLitePaperStore(path):
        pass
    rewrite(path, "ALTER TABLE operations ADD COLUMN extra TEXT")
    with pytest.raises(PersistenceError, match="schema_mismatch"):
        SQLitePaperStore(path)


@pytest.mark.parametrize("path", ["bad\x00path", "//server/share/paper.db", r"\\server\share\paper.db"])
def test_unsupported_remote_and_malformed_paths(path):
    with pytest.raises(PersistenceError, match="invalid_path"):
        SQLitePaperStore(path)


def test_import_has_no_database_network_or_worker_side_effects():
    code = """
import sqlite3, socket, threading
 def_placeholder
sqlite3.connect = denied
socket.socket = denied
threading.Thread.start = denied
import quantlab.persistence
""".replace(" def_placeholder", "def denied(*args, **kwargs):\n    raise AssertionError('import side effect')")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_integrity_failure_records_stable_reason_on_store(tmp_path):
    path = tmp_path / "paper.db"
    owner, store, reference, args, policy = create(path)
    owner.start(command("start"))
    store.close()
    rewrite(path, "UPDATE metadata SET count=2")
    with SQLitePaperStore(path) as store:
        with pytest.raises(PersistenceError, match="journal_tail"):
            DurablePaperSession.recover(store, reference.config, storage_policy=policy, **args)
        assert store.last_recovery_failure == "journal_tail"
        assert store._owner is None
