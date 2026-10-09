"""Operational replay, uncertainty and checkpoint equivalence for admitted entries."""
from decimal import Decimal as D
import json
import pytest
from quantlab.paper import (PaperSession, OrderState as S, PaperIdentityConflict,
    PaperInputError, SessionRecord, stable_id)
from quantlab.paper.models import KernelProgress
from quantlab.paper.strategy_models import record
from quantlab.persistence import (DurablePaperSession, SQLitePaperStore, StoragePolicy,
    PersistenceError, RecoveryRequired)
from quantlab.persistence.contracts import Effects, JournalEntry, decode
from tests.paper.advanced_entry_helpers import configuration, prefix, quote, cancel, T
from tests.paper.test_sessions import command, control
from tests.backtesting.helpers import MINUTE


def setup(path, **kwargs):
    config, owners = configuration(**kwargs)
    policy = StoragePolicy(checkpoint_interval=2)
    store = SQLitePaperStore(path)
    owner = DurablePaperSession(store, config, storage_policy=policy, **owners)
    reference = PaperSession(config, **owners)
    return owner, store, reference, owners, policy


def recover(path, config, owners, policy, checkpoint):
    store = SQLitePaperStore(path)
    try:
        return DurablePaperSession.recover(store, config, storage_policy=policy,
            use_checkpoint=checkpoint, **owners), store
    except BaseException:
        store.close()
        raise


def chain(kind):
    prices = {"market":["102"], "limit":["100","99"],
        "stop_market":["104","104","105"], "stop_limit":["105","105","104","103"]}[kind]
    return (*prefix(), *(quote(n,p) for n,p in enumerate(prices,6)))


@pytest.mark.parametrize("kind", ["market", "limit", "stop_market", "stop_limit"])
@pytest.mark.parametrize("length", [3,4,5,6,7,8])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_restart_each_entry_stage_exact_results_and_operator_gate(tmp_path, kind, length, checkpoint):
    path = tmp_path/"lifecycle.db"
    owner, store, reference, owners, policy = setup(path, kind=kind)
    items = chain(kind)[:length]
    for item in items:
        assert owner.process(item) == reference.process(item)
    expected = owner.snapshot
    owner.checkpoint(); store.close()
    owner, store = recover(path, reference.config, owners, policy, checkpoint)
    assert owner.snapshot == expected == reference.snapshot
    assert owner.operator_required
    for item in items:
        assert owner.process(item) == reference.process(item)
    assert owner.snapshot == expected and owner.operator_required
    with pytest.raises(RecoveryRequired, match="operator_pause_required"):
        owner.process(quote(20,"100",time=T+20*MINUTE))
    assert store.verify()[-1][3].kernel == owner.records[-1].entry_order
    assert type(store.verify()[-1][3].kernel) is KernelProgress
    store.close()


@pytest.mark.parametrize("length", [1,2])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_recover_recorded_admission_and_decision_before_submission(tmp_path, length, checkpoint):
    path = tmp_path/"decision.db"
    owner, store, reference, owners, policy = setup(path)
    for item in prefix()[:length]:
        assert owner.process(item) == reference.process(item)
    expected = owner.snapshot
    owner.checkpoint(); store.close()
    owner, store = recover(path, reference.config, owners, policy, checkpoint)
    assert owner.snapshot == expected
    assert owner.snapshot.execution.kernel.submission is None
    store.close()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("acknowledged", [False, True])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_resting_and_partial_cancellation_restart(tmp_path, partial, acknowledged, checkpoint):
    path = tmp_path/"cancel.db"
    owner, store, reference, owners, policy = setup(path, kind="market" if partial else "limit")
    for item in prefix():
        owner.process(item)
    owner.process(cancel(owner,6))
    if acknowledged:
        owner.process(cancel(owner,7,"ack_cancel_entry"))
    expected = owner.snapshot
    owner.checkpoint(); store.close()
    owner, store = recover(path, owner.config, owners, policy, checkpoint)
    assert owner.snapshot == expected
    assert owner.snapshot.account.fees_paid == (D("1.1") if partial else 0)
    assert bool(owner.snapshot.account.reservations) != acknowledged
    store.close()


@pytest.mark.parametrize("stage", ["before_commit", "commit_armed", "after_commit", "publication"])
@pytest.mark.parametrize("operation", ["submit", "open", "trigger", "partial", "final", "cancel", "ack"])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_durable_failure_windows_no_duplicate_financial_effect(tmp_path, monkeypatch, stage, operation, checkpoint):
    path = tmp_path/"fault.db"
    owner, store, reference, owners, policy = setup(path, kind="stop_market")
    items = list(chain("stop_market"))
    count = {"submit":2, "open":3, "trigger":4, "partial":5, "final":6, "cancel":6, "ack":6}[operation]
    for item in items[:count]:
        assert owner.process(item) == reference.process(item)
    if operation == "ack":
        req = cancel(owner,8,time=T+4*MINUTE)
        assert owner.process(req) == reference.process(req)
    target = {"submit":items[2], "open":items[3], "trigger":items[4], "partial":items[5], "final":items[6],
        "cancel":cancel(owner,8,time=T+4*MINUTE),
        "ack":cancel(owner,9,"ack_cancel_entry",time=T+5*MINUTE)}[operation]
    before = owner.snapshot
    with monkeypatch.context() as patch:
        if stage == "publication":
            def fail(candidate):
                raise KeyboardInterrupt("publication")
            patch.setattr(owner,"_publish_candidate",fail)
        else:
            def fail(actual):
                if actual == stage:
                    raise KeyboardInterrupt(stage)
            patch.setattr(store,"_fault",fail)
        with pytest.raises(KeyboardInterrupt if stage == "before_commit" else RecoveryRequired):
            owner.process(target)
    if stage == "before_commit":
        assert owner.snapshot == before and not owner.recovery_required
        assert owner.process(target) == reference.process(target)
    elif stage != "commit_armed":
        reference.process(target)
    if stage != "before_commit":
        assert owner.recovery_required
        with pytest.raises(RecoveryRequired):
            _ = owner.snapshot
    store.close()
    restored, store = recover(path, reference.config, owners, policy, checkpoint)
    assert restored.snapshot == reference.snapshot
    if stage != "commit_armed":
        assert restored.process(target) == reference.process(target)
        assert restored.snapshot.account.fees_paid == reference.snapshot.account.fees_paid
    else:
        assert restored.snapshot == before
    store.close()


@pytest.mark.parametrize("checkpoint", [False, True])
def test_recovered_resting_entry_resumes_and_oco_still_operates(tmp_path, checkpoint):
    from tests.paper.test_oco import create as oco_submit
    from tests.persistence.test_advanced import q
    path = tmp_path/"oco.db"
    owner, store, reference, owners, policy = setup(path)
    for item in prefix():
        owner.process(item)
    saved = owner.snapshot
    store.close()
    owner, store = recover(path, owner.config, owners, policy, checkpoint)
    assert owner.snapshot == saved and owner.operator_required
    for item in (command("pause",6,T+MINUTE),command("resume",7,T+MINUTE),quote(8,"100"),quote(9,"99")):
        owner.process(item)
    assert owner.snapshot.execution.kernel.state is S.FILLED
    current = owner.snapshot.account.position
    group = oco_submit(owner,n=10,timestamp=owner.snapshot.clock.timestamp)
    owner.process(group)
    for n, price in [(11,"110"),(12,"111")]:
        owner.process(q(n,price,time=owner.snapshot.clock.timestamp+MINUTE))
    expected = owner.snapshot
    assert expected.account.position.quantity == 0 and expected.oco.group.remaining_quantity == 0
    owner.checkpoint(); store.close()
    owner, store = recover(path, owner.config, owners, policy, checkpoint)
    assert owner.snapshot == expected and owner.operator_required
    store.close()


@pytest.mark.parametrize("length", [0,8,64,100])
@pytest.mark.parametrize("checkpoint", [False,True])
def test_durable_entry_hot_path_uses_heads_and_suffix_only(tmp_path, monkeypatch, length, checkpoint):
    from tests.paper.test_history_hot_path import protect_prefix
    from tests.persistence.test_history_hot_path import NoHistoryIteration
    path = tmp_path/"bounded.db"
    owner, store, reference, owners, policy = setup(path, maximum_inputs=256)
    for item in prefix():
        owner.process(item); reference.process(item)
    for n in range(6,6+length):
        item = quote(n,"101")
        owner.process(item); reference.process(item)
    owner._publication.account._journal = NoHistoryIteration(owner._publication.account._journal)
    item = quote(6+length,"100")
    with monkeypatch.context() as patch:
        copies = protect_prefix(patch)
        result = owner.process(item)
        assert owner.process(item) is result
    assert result == reference.process(item)
    entry = store.lookup(item.event_id)
    head = json.loads(entry.effects)["kernel"]
    assert "inputs" not in head and "events" not in head
    assert len(entry.effects) < 30000 and len(entry.output) < 40000
    assert max(copies) <= 16 and len(copies) <= 64*16
    expected = owner.snapshot
    store.close()
    owner, store = recover(path, reference.config, owners, policy, checkpoint)
    assert owner.snapshot == expected == reference.snapshot
    store.close()


@pytest.mark.parametrize("field", ["history_digest", "filled_quantity", "event_count"])
@pytest.mark.parametrize("checkpoint", [False,True])
def test_hash_consistent_forged_entry_heads_cannot_restore_authority(tmp_path, field, checkpoint):
    from tests.persistence.test_paper import rewrite
    from quantlab.persistence.store import entry_digest
    path = tmp_path/"forged.db"
    owner, store, reference, owners, policy = setup(path)
    for item in (*prefix(),quote(6,"100")):
        owner.process(item)
    entry, _, out, effects = store.verify()[-1]
    head = out.entry_order
    replacement = {"history_digest":"f"*64, "filled_quantity":D("0.5"), "event_count":head.event_count+1}[field]
    forged = head.model_copy(update={field:replacement})
    out = record(SessionRecord, **(out.model_dump(exclude={"record_id"})|{"entry_order":forged}))
    effects = effects.model_copy(update={"kernel":forged})
    config = owner.config
    store.close()
    changed = entry.model_copy(update={"output":out.canonical_json(), "effects":effects.canonical_json(),
        "output_digest":stable_id("paper-durable-output-v1",(out,effects))})
    changed = changed.model_copy(update={"digest":entry_digest(changed)})
    rewrite(path,"UPDATE operations SET entry=?,digest=? WHERE ordinal=?",(changed.canonical_json(),changed.digest,changed.ordinal))
    rewrite(path,"UPDATE metadata SET head=?",(changed.digest,))
    # No checkpoint at this final ordinal; full suffix replay must verify the head.
    with pytest.raises(PersistenceError):
        recover(path,config,owners,policy,checkpoint)


@pytest.mark.parametrize("length", [0,8,64,100])
@pytest.mark.parametrize("checkpoint", [False,True])
def test_long_partial_financial_journal_never_copies_prefix(tmp_path, monkeypatch, length, checkpoint):
    from tests.paper.test_history_hot_path import protect_prefix
    from tests.persistence.test_history_hot_path import NoHistoryIteration
    path=tmp_path/"partial-history.db"
    owner,store,reference,owners,policy=setup(path,kind="market",quantity=D("250"),capital="50000",maximum_inputs=256)
    for item in prefix():
        assert owner.process(item)==reference.process(item)
    for n in range(6,6+length):
        item=quote(n,"101")
        assert owner.process(item)==reference.process(item)
    account=owner._publication.account
    account._journal=NoHistoryIteration(account._journal)
    indexes={name:getattr(account,name) for name in ("_journal","_seen","_adapter_seen",
        "_reservation_ids","_reserved_orders","_settled_orders","_execution_ids")}
    item=quote(6+length,"101")
    with monkeypatch.context() as patch:
        copies=protect_prefix(patch)
        out=owner.process(item)
        count=len(copies)
        assert owner.process(item) is out and len(copies)==count
    assert out.entry_order.filled_quantity==length+2
    assert out.account.fees_paid==D("1.1")*(length+2)
    assert all(getattr(owner._publication.account,name) is index for name,index in indexes.items())
    assert max(copies)<=16 and len(copies)<=64*16
    assert out==reference.process(item)
    expected=owner.snapshot
    store.close()
    owner,store=recover(path,reference.config,owners,policy,checkpoint)
    assert owner.snapshot==expected==reference.snapshot
    store.close()


@pytest.mark.parametrize("partial", [False,True])
@pytest.mark.parametrize("checkpoint", [False,True])
def test_stop_completes_existing_entry_cancel_at_capacity_and_recovers(tmp_path, partial, checkpoint):
    path=tmp_path/"capacity.db"
    owner,store,reference,owners,policy=setup(path,kind="market" if partial else "limit",maximum_inputs=3)
    for item in prefix():
        owner.process(item)
    owner.process(cancel(owner,6,time=T+MINUTE))
    before=owner.snapshot
    out=owner.stop(command("stop",7,T+MINUTE))
    assert out.entry_order.input_count==5 and out.entry_order.state is S.CANCELLED
    assert out.account.position==before.account.position and not out.account.reservations
    expected=owner.snapshot
    owner.checkpoint();store.close()
    owner,store=recover(path,owner.config,owners,policy,checkpoint)
    assert owner.snapshot==expected and not owner.operator_required
    store.close()


@pytest.mark.parametrize("version", [0,1,2,4])
def test_manifest_cannot_select_advanced_entries_under_wrong_engine_or_config_version(version):
    from quantlab.persistence.store import Manifest
    config,owners=configuration()
    manifest=dict(engine_version="paper-18f-exits-v2",config=config,policy=StoragePolicy(),
        owner_digest="0"*64,admission_digest="0"*64,binding_digest="0"*64)
    with pytest.raises(ValueError):
        Manifest(**manifest)
    with pytest.raises(ValueError):
        type(config).model_validate(config.model_copy(update={"schema_version":version}))
