"""OCO journal/checkpoint reconstruction, atomic fault windows and tampering."""
from decimal import Decimal as D
import pytest
from quantlab.paper import PaperSession, OrderState as S
from quantlab.persistence import (DurablePaperSession, SQLitePaperStore, RecoveryRequired, PersistenceError, StoragePolicy)
from tests.persistence.test_advanced import setup, execute, q, reopened, T, MINUTE
from tests.paper.test_oco import create, cancel
from tests.paper.test_sessions import command


@pytest.mark.parametrize("checkpoint",[False,True])
@pytest.mark.parametrize("case",["create","trigger","partial_stop","partial_target","stop_close","target_close","mixed_close","request","ack","stop_session"])
def test_recover_each_oco_boundary(tmp_path,checkpoint,case):
    path=tmp_path/"oco.db"
    owner,store,reference,owners,policy=setup(path)
    execute(owner); execute(reference)
    items=[create(owner)]
    if case in ("trigger","partial_stop","stop_close"):
        items += [q(7,"95")]
        if case != "trigger": items += [q(8,"94")]
        if case == "stop_close": items += [q(9,"93")]
    elif case in ("partial_target","target_close","mixed_close","stop_session"):
        items += [q(7,"110")]
        if case == "target_close": items += [q(8,"111")]
        elif case == "mixed_close": items += [q(8,"95"),q(9,"94")]
        elif case == "stop_session": items += [command("stop",8,T+3*MINUTE)]
    for item in items:
        assert owner.process(item) == reference.process(item)
    if case in ("request","ack"):
        req=cancel(owner,7); items.append(req)
        assert owner.process(req) == reference.process(req)
        if case == "ack":
            ack=cancel(owner,8,"ack_cancel_oco"); items.append(ack)
            assert owner.process(ack) == reference.process(ack)
    owner.checkpoint()
    expected=owner.snapshot
    wires=[row[0].canonical_json() for row in store.verify()]
    config=owner.config; store.close()
    restored,store=reopened(path,config,owners,policy,checkpoint)
    assert restored.snapshot == expected == reference.snapshot
    assert [row[0].canonical_json() for row in store.verify()] == wires
    for item in items:
        assert restored.process(item) == reference.process(item)
    assert restored.snapshot == expected
    if case in ("partial_stop","partial_target"):
        assert restored.snapshot.account.position.quantity == 1
        restored.process(command("pause",10,T+4*MINUTE))
        restored.process(command("resume",11,T+4*MINUTE))
        out=restored.process(q(12,"93" if case == "partial_stop" else "111",time=T+5*MINUTE))
        assert out.oco.state == "closed" and out.account.position.quantity == 0
        assert out.account.fees_paid == D("3.4")
    store.close()


@pytest.mark.parametrize("stage",["before_commit","commit_armed","after_commit","publication"])
@pytest.mark.parametrize("operation",["create","trigger","partial_stop","partial_target","stop_close","target_close","request","ack"])
def test_oco_durable_fault_windows(tmp_path,monkeypatch,stage,operation):
    path=tmp_path/"fault.db"
    owner,store,reference,owners,policy=setup(path)
    execute(owner); execute(reference)
    created=create(owner)
    items=[created]
    if operation in ("trigger","partial_stop","stop_close"):
        items += [q(7,"95"),q(8,"94"),q(9,"93")]
        index={"trigger":1,"partial_stop":2,"stop_close":3}[operation]
    elif operation in ("partial_target","target_close"):
        items += [q(7,"110"),q(8,"111")]
        index=1 if operation == "partial_target" else 2
    else:
        index=0
    for item in items[:index]: owner.process(item); reference.process(item)
    if operation in ("request","ack"):
        if index == 0:
            owner.process(created); reference.process(created)
        req=cancel(owner,7)
        if operation == "request": target=req
        else:
            owner.process(req); reference.process(req)
            target=cancel(owner,8,"ack_cancel_oco")
    else: target=items[index]
    before=owner.snapshot
    with monkeypatch.context() as patch:
        def fail(actual):
            if actual == stage: raise KeyboardInterrupt(stage)
        if stage == "publication": patch.setattr(owner,"_publish_candidate",lambda candidate: fail("publication"))
        else: patch.setattr(store,"_fault",fail)
        with pytest.raises(KeyboardInterrupt if stage == "before_commit" else RecoveryRequired): owner.process(target)
    if stage == "before_commit":
        assert owner.snapshot == before and not owner.recovery_required
        owner.process(target)
    else:
        assert owner.recovery_required
        with pytest.raises(RecoveryRequired): _=owner.snapshot
    reference.process(target)
    if stage == "commit_armed":
        # Outcome is unknown to the interrupted owner; SQLite confirmed rollback
        # when reopening, so recovered state is the pre-input reference.
        expected=before
    else: expected=reference.snapshot
    config=owner.config; store.close()
    restored,store=reopened(path,config,owners,policy,True)
    assert restored.snapshot == expected
    if stage != "commit_armed": assert restored.process(target) == reference.process(target)
    store.close()


@pytest.mark.parametrize("length",[0,8,64,100])
@pytest.mark.parametrize("checkpoint",[False,True])
def test_oco_hot_path_has_constant_index_work_and_bounded_wires(tmp_path,monkeypatch,length,checkpoint):
    import json
    from tests.persistence.test_history_hot_path import large_session, NoHistoryIteration
    from tests.paper.test_history_hot_path import protect_prefix
    from quantlab.persistence.contracts import Effects, decode
    path=tmp_path/"scaling.db"
    owner,store,reference,owners,policy=large_session(path)
    cmd=create(owner)
    assert owner.process(cmd) == reference.process(cmd)
    for n in range(7,7+length): assert owner.process(q(n)) == reference.process(q(n))
    before=owner.snapshot
    account=owner._publication.account
    account._journal=NoHistoryIteration(account._journal)
    item=q(7+length)
    with monkeypatch.context() as patch:
        copies=protect_prefix(patch)
        result=owner.process(item)
        assert owner.process(item) is result
    # Two markets + two child retries + group retry + 3 events + child/account
    # liquidity, plus two discarded staging paths when extending the sibling:
    # twelve 64-level paths, independent of retained history length.
    assert len(copies) == 64*12
    assert max(copies) <= 16 and sum(copies) <= 64*12*16
    assert result.oco.remaining_quantity == 249-length
    entry=store.lookup(item.event_id)
    head=json.loads(entry.effects)["oco"]
    assert "events" not in head["stop"] and "inputs" not in head["target"]
    assert len(entry.effects)<30000 and len(entry.output)<60000
    assert decode(Effects,entry.effects).oco == result.oco
    assert before.oco.group.target.filled_quantity == length
    assert result == reference.process(item)
    expected=owner.snapshot; config=owner.config; store.close()
    restored,store=reopened(path,config,owners,policy,checkpoint)
    assert restored.snapshot == expected == reference.snapshot
    store.close()


@pytest.mark.parametrize("field",["history_digest","costs","quantities","revision"])
@pytest.mark.parametrize("checkpoint",[False,True])
def test_hash_consistent_forged_oco_heads_fail_operational_recovery(tmp_path,field,checkpoint):
    from quantlab.paper.strategy_models import record
    from quantlab.paper.session_models import SessionRecord
    from quantlab.paper.oco_models import OCOEvent
    from quantlab.paper.models import stable_id
    from quantlab.persistence.store import entry_digest, checkpoint_digest
    from quantlab.persistence.contracts import JournalEntry
    from tests.persistence.test_paper import rewrite
    path=tmp_path/"tamper.db"
    owner,store,_,owners,policy=setup(path)
    execute(owner); owner.process(create(owner)); owner.process(q(7,"110"))
    cp=owner.checkpoint()
    entry,_,out,effects=store.verify()[-1]
    h=out.oco
    if field == "history_digest":
        h=h.model_copy(update={"stop":h.stop.model_copy(update={"history_digest":"f"*64})})
    elif field == "costs":
        h=h.model_copy(update={"target":h.target.model_copy(update={"cumulative_costs":h.target.cumulative_costs.model_copy(update={"fees":D("12")})})})
    elif field == "quantities":
        h=h.model_copy(update={"remaining_quantity":D("1.5"),
            "target":h.target.model_copy(update={"filled_quantity":D("0.5")}),
            "stop":h.stop.model_copy(update={"withdrawn_quantity":D("0.5")})})
    else: h=h.model_copy(update={"revision":h.revision+1})
    ev=out.oco_events[0]
    ev=record(OCOEvent,**{**ev.model_dump(exclude={"record_id"}),"group":h,
        "previous_revision":h.revision-1})
    out=record(SessionRecord,**{**out.model_dump(exclude={"record_id"}),"oco":h,"oco_events":(ev,)})
    effects=effects.model_copy(update={"oco":h})
    config=owner.config; store.close()
    changed=JournalEntry(**{**entry.model_dump(),"output":out.canonical_json(),"effects":effects.canonical_json(),
        "output_digest":stable_id("paper-durable-output-v1",(out,effects))})
    changed=changed.model_copy(update={"digest":entry_digest(changed)})
    rewrite(path,"UPDATE operations SET entry=?,digest=? WHERE ordinal=?",(changed.canonical_json(),changed.digest,changed.ordinal))
    rewrite(path,"UPDATE metadata SET head=?",(changed.digest,))
    cp=cp.model_copy(update={"oco":h,"head_digest":changed.digest,"record_id":out.record_id})
    cp=cp.model_copy(update={"digest":checkpoint_digest(cp)})
    rewrite(path,"UPDATE checkpoints SET payload=?,digest=? WHERE ordinal=?",(cp.canonical_json(),cp.digest,cp.ordinal))
    store=SQLitePaperStore(path)
    try:
        with pytest.raises(PersistenceError,match="replay_mismatch|checkpoint_operation_mismatch"):
            DurablePaperSession.recover(store,config,storage_policy=policy,use_checkpoint=checkpoint,**owners)
    finally: store.close()


@pytest.mark.parametrize("checkpoint",[False,True])
def test_frozen_pre_oco_v2_journal_recovers_without_rewriting(tmp_path,checkpoint):
    import json
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures'/'phase18f-v2-before-oco.json').read_text(encoding='utf-8'))
    store=SQLitePaperStore(tmp_path/'original-v2.db')
    for table in ('metadata','operations','checkpoints'):
        for row in fixture[table]:
            store._connection.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})",row)
    reference,owners=setup()
    policy=StoragePolicy(checkpoint_interval=2)
    owner=DurablePaperSession.recover(store,reference.config,storage_policy=policy,use_checkpoint=checkpoint,**owners)
    assert owner.snapshot.canonical_json() == fixture['snapshot']
    assert owner.snapshot.oco is None
    assert [e.canonical_json() for e,_,_,_ in store.verify()] == [row[-1] for row in fixture['operations']]
    assert [c.canonical_json() for c in store.checkpoints()] == [row[-1] for row in fixture['checkpoints']]
    store.close()


def test_durable_stop_at_default_capacity_with_pending_cancel_recovers_both_modes(tmp_path):
    from tests.persistence.test_history_hot_path import large_session
    path=tmp_path/'capacity.db'
    owner,store,reference,owners,policy=large_session(path,interval=20)
    cmd=create(owner)
    assert owner.process(cmd) == reference.process(cmd)
    for n in range(7,133): assert owner.process(q(n,"100")) == reference.process(q(n,"100"))
    assert owner.snapshot.oco.group.stop.input_count == 128
    req=cancel(owner,133)
    assert owner.process(req) == reference.process(req)
    stop=command("stop",134,T+129*MINUTE)
    result=owner.process(stop)
    assert result == reference.process(stop)
    assert result.oco.state == "cancelled" and len(result.oco_events) == 1
    assert result.oco.stop.input_count == result.oco.target.input_count == 130
    assert result.account.position.quantity == 250 and not result.financial
    owner.checkpoint(); expected=owner.snapshot; config=owner.config; store.close()
    for checkpoint in (False,True):
        restored,store=reopened(path,config,owners,policy,checkpoint)
        assert restored.snapshot == expected == reference.snapshot
        assert restored.process(stop) == result
        store.close()
