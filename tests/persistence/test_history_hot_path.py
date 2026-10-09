"""Durable hot paths emit bounded heads; recovery verifies complete prefixes."""
from decimal import Decimal as D
import json

import pytest

from quantlab.paper import AdvancedReplayConfig, PaperSession
from quantlab.paper.models import KernelProgress, KernelSnapshot
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy, PersistenceError
from quantlab.persistence.contracts import Effects, decode
from tests.paper.test_sessions import session
from tests.paper.test_history_hot_path import protect_prefix
from tests.persistence.test_advanced import execute, exit_command, q, reopened
from tests.backtesting.helpers import CONFIG, MINUTE


class NoHistoryIteration(list):
    def __iter__(self):
        raise AssertionError("candidate copied/traversed financial journal")


def large_session(path, interval=2):
    base, owners = session(age=10*MINUTE, maximum_inputs=300, capital="50000",
        research_config=CONFIG.model_copy(update={"quantity": D("250")}))
    config = AdvancedReplayConfig(**base.config.model_dump(exclude={"schema_version"}),
        liquidity_per_observation=D("1"))
    store = SQLitePaperStore(path)
    policy = StoragePolicy(checkpoint_interval=interval)
    owner = DurablePaperSession(store, config, storage_policy=policy, **owners)
    reference = PaperSession(config, **owners)
    execute_large(owner); execute_large(reference)
    return owner, store, reference, owners, policy


def execute_large(owner):
    from tests.paper.test_sessions import inputs
    for item in inputs():
        owner.process(item)
    assert owner.snapshot.account.position.quantity == 250


@pytest.mark.parametrize("length", [0, 8, 64, 100])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_durable_partial_scaling_and_recovery(tmp_path, monkeypatch, length, checkpoint):
    owner, store, reference, owners, policy = large_session(tmp_path / "heads.db")
    command = exit_command(owner, quantity=D("250"))
    assert owner.process(command) == reference.process(command)
    for n in range(7, 7+length):
        assert owner.process(q(n)) == reference.process(q(n))
    before = owner.snapshot
    account = owner._publication.account
    account._journal = NoHistoryIteration(account._journal)
    caches = {name: getattr(account, name) for name in ("_journal", "_seen", "_adapter_seen",
        "_reservation_ids", "_reserved_orders", "_settled_orders", "_execution_ids")}
    item = q(7+length)
    with monkeypatch.context() as patch:
        copies = protect_prefix(patch)
        outcome = owner.process(item)
    assert all(getattr(owner._publication.account, name) is index for name, index in caches.items())
    assert isinstance(outcome.exit_order, KernelProgress)
    assert outcome.exit_order.filled_quantity == length+1
    assert len(copies) == 64 * 6  # Two events, retry, market, kernel/account liquidity.
    assert max(copies) <= 16
    # Head/output wire contains the new events once, never the retained prefix.
    entry = store.lookup(item.event_id)
    head = json.loads(entry.effects)["exit_kernel"]
    assert "inputs" not in head and "events" not in head
    assert head["event_count"] == owner._publication.exit_kernel._view.events.count
    assert isinstance(decode(Effects, entry.effects).exit_kernel, KernelProgress)
    assert len(entry.output) < 20000 and len(entry.effects) < 16000
    assert len(before.exit_order.events) < head["event_count"]
    # Consumer snapshots still expose all records and remain frozen.
    expected = owner.snapshot
    assert outcome == reference.process(item)
    store.close()
    owner, store = reopened(tmp_path / "heads.db", reference.config, owners, policy, checkpoint)
    assert owner.snapshot == expected == reference.snapshot
    assert owner.process(item) == outcome
    assert owner.operator_required
    store.close()


@pytest.mark.parametrize("checkpoint", [False, True])
def test_legacy_v2_snapshot_records_and_mixed_head_suffix_recover(tmp_path, checkpoint):
    from tests.persistence.test_advanced import setup, execute
    path = tmp_path / "legacy-v2.db"
    owner, store, reference, owners, policy = setup(path)
    execute(owner)
    owner._legacy_exit_record = True
    command = exit_command(owner, order_type="stop_market", stop_price=D("96"), protective_role="stop_loss")
    for item in (command, q(7, "95"), q(8, "94")):
        result = owner.process(item)
        assert isinstance(result.exit_order, KernelSnapshot)
    old_wires = [row[0].canonical_json() for row in store.verify()]
    expected = owner.snapshot
    store.close()
    owner, store = reopened(path, expected.config, owners, policy, checkpoint)
    assert owner.snapshot == expected
    assert [row[0].canonical_json() for row in store.verify()] == old_wires
    from tests.paper.test_sessions import command as lifecycle
    from tests.paper.test_sessions import T
    owner.process(lifecycle("pause", 9, T+3*MINUTE))
    owner.process(lifecycle("resume", 10, T+3*MINUTE))
    result = owner.process(q(11, "93"))
    assert isinstance(result.exit_order, KernelProgress)
    expected = owner.snapshot
    store.close()
    owner, store = reopened(path, expected.config, owners, policy, checkpoint)
    assert owner.snapshot == expected and owner.snapshot.account.position.quantity == 0
    store.close()


@pytest.mark.parametrize("target", ["record", "publication"])
def test_failed_session_candidate_undoes_only_new_financial_indexes(tmp_path, monkeypatch, target):
    owner, store, reference, _, _ = large_session(tmp_path / "rollback.db")
    command = exit_command(owner, quantity=D("250"))
    owner.process(command); reference.process(command)
    for n in range(7, 40):
        assert owner.process(q(n)) == reference.process(q(n))
    before = reference.snapshot
    account = reference._publication.account
    names = ("_seen", "_adapter_seen", "_reservation_ids", "_reserved_orders", "_settled_orders", "_execution_ids")
    indexes = {name: getattr(account, name).copy() for name in names}
    journal = account.events
    method = "_prepare_record" if target == "record" else "_publish_candidate"
    original = getattr(reference, method)
    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("injected after staging")
    with monkeypatch.context() as patch:
        patch.setattr(reference, method, interrupt)
        with pytest.raises(KeyboardInterrupt):
            reference.process(q(40))
    assert reference.snapshot == before and account.events == journal
    assert all(getattr(account, name) == value for name, value in indexes.items())
    assert reference.process(q(40)) == owner.process(q(40))
    assert reference.snapshot == owner.snapshot
    store.close()


@pytest.mark.parametrize("field", ["history_digest", "input_count", "event_count", "filled_quantity", "cumulative_costs"])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_recovery_rejects_hash_consistent_forged_progress(tmp_path, field, checkpoint):
    from quantlab.paper.strategy_models import record
    from quantlab.paper.session_models import SessionRecord
    from tests.persistence.test_paper import rewrite
    from quantlab.persistence.store import entry_digest, checkpoint_digest
    from quantlab.persistence.contracts import JournalEntry, Checkpoint
    from quantlab.paper.models import stable_id
    from tests.persistence.test_advanced import setup, execute
    path = tmp_path / "forged-head.db"
    owner, store, _, owners, policy = setup(path)
    execute(owner)
    owner.process(exit_command(owner))
    owner.process(q(7))
    cp = owner.checkpoint()
    entry, _, out, effects = store.verify()[-1]
    head = out.exit_order
    replacement = {"history_digest": "f"*64, "input_count": head.input_count+1,
        "event_count": head.event_count+1, "filled_quantity": D("0.5"),
        "cumulative_costs": head.cumulative_costs.model_copy(update={"fees": D("12")})}[field]
    forged = head.model_copy(update={field: replacement})
    values = out.model_dump(exclude={"record_id", "schema_version"})
    values["exit_order"] = forged
    out = record(SessionRecord, **values)
    effects = effects.model_copy(update={"exit_kernel": forged})
    cp = cp.model_copy(update={"exit_kernel": forged})
    config = owner.config
    store.close()
    # Recompute every content/durable/checkpoint hash, so digests alone pass.
    values = entry.model_dump()
    values.update(output=out.canonical_json(), effects=effects.canonical_json(),
                  output_digest=stable_id("paper-durable-output-v1", (out, effects)))
    changed = JournalEntry(**values)
    changed = changed.model_copy(update={"digest": entry_digest(changed)})
    rewrite(path, "UPDATE operations SET entry=?,digest=? WHERE ordinal=?",
            (changed.canonical_json(), changed.digest, changed.ordinal))
    rewrite(path, "UPDATE metadata SET head=?", (changed.digest,))
    cp = cp.model_copy(update={"head_digest": changed.digest, "record_id": out.record_id})
    cp = cp.model_copy(update={"digest": checkpoint_digest(cp)})
    rewrite(path, "UPDATE checkpoints SET payload=?,digest=? WHERE ordinal=?",
            (cp.canonical_json(), cp.digest, cp.ordinal))
    store = SQLitePaperStore(path)
    try:
        with pytest.raises(PersistenceError, match="replay_mismatch|checkpoint_operation_mismatch"):
            DurablePaperSession.recover(store, config, storage_policy=policy,
                use_checkpoint=checkpoint, **owners)
    finally:
        store.close()
