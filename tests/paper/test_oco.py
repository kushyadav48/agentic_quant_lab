"""Atomic two-child OCO: prices, quantity, accounting and bounded coordination."""
from decimal import Decimal as D
import pytest
from quantlab.paper import (OCOCommand, OCOFillRecord, OCOQuantityAdjustment,
    OrderState as S, PaperInputError, PaperIdentityConflict)
from tests.persistence.test_advanced import setup, execute, q, T, MINUTE
from tests.paper.test_sessions import command, event


def create(owner, n=6, **changes):
    p = owner.snapshot.account.position
    body = dict(command_id=f"oco-{n}", sequence=n, timestamp=T+MINUTE,
        action="submit_oco", reason_reference="human:protect", account_id=p.account_id,
        strategy_id=p.strategy_id, instrument_id=p.instrument.instrument_id,
        position_id=p.entry_transaction_id, quantity=p.quantity, stop_price=D("96"), target_price=D("105"))
    body.update(changes)
    return OCOCommand(**body)


def cancel(owner, n, action="request_cancel_oco", **changes):
    h = owner.snapshot.oco.group
    body = dict(command_id=f"{action}-{n}", sequence=n, timestamp=T+(n-5)*MINUTE,
        action=action, reason_reference="human:cancel", account_id=h.account_id,
        strategy_id=h.strategy_id, instrument_id=h.instrument_id, position_id=h.position_id, group_id=h.group_id)
    body.update(changes)
    return OCOCommand(**body)


@pytest.mark.parametrize("stop", [False, True])
def test_partial_and_complete_exit_hand_calculated(stop):
    owner, _ = setup()
    execute(owner)
    creation = owner.process(create(owner))
    assert creation.oco.state == "active"
    assert owner.snapshot.account.reserved_funds == 0
    assert creation.oco.stop.submission.reduce_only and creation.oco.target.submission.reduce_only
    if stop:
        trigger = owner.process(q(7,"95"))
        assert trigger.oco.stop.trigger is not None and not trigger.financial
        first, final, prices = 8, 9, ("94", "93")
    else:
        first, final, prices = 7, 8, ("110", "111")
    before = owner.snapshot
    partial = owner.process(q(first,prices[0]))
    h = partial.oco
    winner, sibling = (h.stop,h.target) if stop else (h.target,h.stop)
    assert winner.state is S.PARTIALLY_FILLED and sibling.state is S.ACCEPTED
    assert winner.filled_quantity == sibling.withdrawn_quantity == 1
    assert h.remaining_quantity == winner.remaining_quantity == sibling.remaining_quantity == 1
    assert before.account.position.quantity == 2 and before.oco.group.remaining_quantity == 2
    assert partial.account.position.quantity == 1 and partial.account.position_collateral == 101
    assert partial.account.fees_paid == D("2.3")
    assert partial.account.realized_pnl == D("-7" if stop else "9")
    assert sum(isinstance(e,OCOQuantityAdjustment) for e in partial.orders) == 1
    fill_index=next(i for i,e in enumerate(partial.orders) if isinstance(e,OCOFillRecord))
    adjustment_index=next(i for i,e in enumerate(partial.orders) if isinstance(e,OCOQuantityAdjustment))
    assert fill_index < adjustment_index
    result = owner.process(q(final,prices[1]))
    h = result.oco
    winner, sibling = (h.stop,h.target) if stop else (h.target,h.stop)
    assert h.state == "closed" and h.remaining_quantity == 0
    assert winner.state is S.FILLED and sibling.state is S.CANCELLED
    assert winner.filled_quantity == 2 and sibling.withdrawn_quantity == 2 and sibling.filled_quantity == 0
    assert result.account.position.quantity == 0 and result.account.position_collateral == 0
    assert result.account.fees_paid == D("3.4")
    assert result.account.realized_pnl == D("-15" if stop else "19")
    assert result.account.available_funds == D("981.6" if stop else "1015.6")
    assert owner.snapshot.oco.group == h


@pytest.mark.parametrize("changes", [dict(account_id="other"), dict(strategy_id="other"),
    dict(instrument_id="OTHER"), dict(position_id="other"), dict(quantity=D("1")),
    dict(quantity=D("3")), dict(stop_price=D("101")), dict(target_price=D("100")),
    dict(sequence=4), dict(timestamp=T), dict(timestamp=T+20*MINUTE)])
def test_creation_rejects_mismatched_or_ambiguous_ownership(changes):
    owner, _ = setup(); execute(owner)
    before = owner.snapshot
    with pytest.raises((PaperInputError, ValueError)):
        owner.process(create(owner, **changes))
    assert owner.snapshot == before
    assert len(owner._publication.account._kernels) == 1


def test_overlap_and_independent_child_authority_are_rejected():
    from tests.persistence.test_advanced import exit_command
    owner, _ = setup(); execute(owner)
    owner.process(create(owner)); before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(create(owner, n=7))
    with pytest.raises(PaperInputError):
        owner.process(exit_command(owner,n=7))
    account = owner._publication.account
    for child in (before.oco.group.stop,before.oco.group.target):
        kernel = account._kernels[child.config.session_id][0]
        with pytest.raises(PaperInputError, match="OCO coordination"):
            kernel.process(q(7).observation)
        with pytest.raises(PaperInputError):
            account.reserve_order(kernel,event_id="reserve-child",sequence=10,timestamp=T+MINUTE)
    assert owner.snapshot == before


@pytest.mark.parametrize("stop", [False, True])
def test_complete_single_observation_exit_cancels_sibling(stop):
    owner, _ = setup(budget=None); execute(owner); owner.process(create(owner))
    if stop:
        owner.process(q(7,"95"))
    out = owner.process(q(8 if stop else 7,"93" if stop else "110"))
    assert out.oco.state == "closed" and out.account.position.quantity == 0
    assert len(out.financial) == 1
    assert out.account.fees_paid == D("2.4")
    assert out.account.realized_pnl == D("-16" if stop else "18")
    assert sum(isinstance(e,OCOFillRecord) for e in out.orders) == 1


def test_target_then_stop_reconciles_both_children_without_erasing_fills():
    owner, _ = setup(); execute(owner); owner.process(create(owner))
    owner.process(q(7,"110"))
    retained = owner.snapshot.oco.target.events
    owner.process(q(8,"95"))
    out = owner.process(q(9,"94"))
    assert out.oco.state == "closed" and out.oco.stop.state is S.FILLED
    assert out.oco.target.state is S.CANCELLED
    assert out.oco.stop.filled_quantity == out.oco.target.filled_quantity == 1
    assert out.oco.stop.withdrawn_quantity == out.oco.target.withdrawn_quantity == 1
    assert out.account.realized_pnl == 2 and out.account.fees_paid == D("3.4")
    assert owner.snapshot.oco.target.events[:len(retained)] == retained


@pytest.mark.parametrize("order", ["stop_first", "target_first"])
def test_equal_timestamp_uses_recorded_sequence_and_stop_priority(order):
    owner, _ = setup(budget=None); execute(owner); owner.process(create(owner))
    stamp = T+2*MINUTE
    if order == "stop_first":
        trigger = owner.process(q(7,"95",time=stamp))
        assert not trigger.financial
        # After triggering, stop-market is executable even after the quote rebounds.
        out = owner.process(q(8,"110",time=stamp))
        assert out.oco.stop.state is S.FILLED and out.oco.target.state is S.CANCELLED
    else:
        owner.process(q(7,"110",time=stamp))
        out = owner.process(q(8,"95",time=stamp))
        assert out.oco.target.state is S.FILLED and out.oco.stop.state is S.CANCELLED
        assert not out.oco_events  # Closed group does not match again.
    assert out.account.position.quantity == 0


def test_shared_observation_budget_is_not_replenished_by_redelivery():
    owner, _ = setup(); execute(owner); owner.process(create(owner))
    first = q(7,"110"); owner.process(first)
    obs = first.observation.model_copy(update={"event_id":"redelivery", "sequence":8})
    out = owner.process(event(obs))
    assert not out.financial and out.oco.remaining_quantity == 1
    assert out.oco.stop.filled_quantity == 0 and out.oco.target.filled_quantity == 1
    out = owner.process(q(9,"111",time=T+3*MINUTE))
    assert out.oco.remaining_quantity == 0


@pytest.mark.parametrize("race", ["partial", "complete", "no_fill"])
def test_cancellation_races_keep_committed_reductions(race):
    owner, _ = setup(budget=None if race == "complete" else D("1"))
    execute(owner); owner.process(create(owner))
    out = owner.process(cancel(owner,7))
    assert out.oco.state == "cancel_pending"
    if race != "no_fill":
        owner.process(q(8,"110",time=T+3*MINUTE))
    out = owner.process(cancel(owner,9,"ack_cancel_oco"))
    assert out.oco.state == ("closed" if race == "complete" else "cancelled")
    assert out.account.position.quantity == (0 if race == "complete" else 1 if race == "partial" else 2)
    assert owner.process(out.command) is out
    if race == "partial":
        assert out.oco.target.filled_quantity == 1 and out.oco.stop.withdrawn_quantity == 1
    before = owner.snapshot
    owner.process(q(10,"130"))
    assert owner.snapshot.account == before.account


def test_exact_retries_conflicting_identity_and_cancellation_ownership():
    owner, _ = setup(); execute(owner)
    cmd = create(owner); created = owner.process(cmd)
    quote = q(7,"110"); partial = owner.process(quote)
    assert owner.process(cmd) is created and owner.process(quote) is partial
    before = owner.snapshot
    for bad in (cmd.model_copy(update={"target_price":D("106")}),
            quote.model_copy(update={"timestamp":T+10*MINUTE}),
            cmd.model_copy(update={"quantity":D("-1")})):
        with pytest.raises(PaperIdentityConflict):
            owner.process(bad)
    for bad in (cancel(owner,8,"ack_cancel_oco"),cancel(owner,8,position_id="wrong"),
            cancel(owner,8,group_id="f"*64)):
        with pytest.raises(PaperInputError):
            owner.process(bad)
    assert owner.snapshot == before


@pytest.mark.parametrize("mode", ["old_observation", "stale", "unavailable", "backward", "future_cause"])
def test_no_stale_unavailable_or_future_causal_execution(mode):
    owner, _ = setup(); execute(owner)
    owner.process(create(owner, timestamp=T+2*MINUTE if mode == "old_observation" else T+MINUTE))
    if mode == "old_observation":
        item = q(7,"110",time=T+3*MINUTE,age=2*MINUTE)
    elif mode == "stale":
        item = q(7,"110",time=T+20*MINUTE,age=19*MINUTE)
    elif mode == "unavailable":
        base = q(7,"110")
        quote = base.observation.quote.model_copy(update={"available_at":T+30*MINUTE})
        item = base.model_copy(update={"observation":base.observation.model_copy(update={"quote":quote})})
    elif mode == "backward":
        owner.process(q(7,"100"))
        item = q(8,"110",time=T+MINUTE)
    else:
        # Producers cannot supply a pre-triggered child or a future fill as authority.
        item = create(owner,n=7).model_copy(update={"trigger_id":"future"})
    before = owner.snapshot
    if mode in ("old_observation", "stale"):
        owner.process(item)
        assert owner.snapshot.account == before.account
        assert owner.snapshot.oco.group.target.filled_quantity == 0
    else:
        with pytest.raises((PaperInputError, ValueError)):
            owner.process(item)
        assert owner.snapshot == before


@pytest.mark.parametrize("seam", ["reconciliation", "event", "account_staging", "session_staging", "publication"])
@pytest.mark.parametrize("stop", [False, True])
def test_failure_before_publication_leaves_no_phantom_fill(monkeypatch,seam,stop):
    import quantlab.paper.oco as oco
    from quantlab.paper.accounts import PaperAccount
    owner, _ = setup(); execute(owner); owner.process(create(owner))
    if stop:
        owner.process(q(7,"95"))
    item = q(8 if stop else 7,"94" if stop else "110")
    before = owner.snapshot
    def fail(*args,**kwargs):
        raise KeyboardInterrupt("injected")
    with monkeypatch.context() as patch:
        if seam == "reconciliation": patch.setattr(oco,"_reconcile",fail)
        elif seam == "event": patch.setattr(oco,"_event",fail)
        elif seam == "account_staging":
            original = PaperAccount._stage_indexes
            def stage(self, financial):
                original(self,financial); fail()
            patch.setattr(PaperAccount,"_stage_indexes",stage)
        elif seam == "session_staging":
            original = owner._stage_record
            def stage(*args): original(*args); fail()
            patch.setattr(owner,"_stage_record",stage)
        else:
            patch.setattr(owner,"_publish_candidate",fail)
        with pytest.raises(KeyboardInterrupt): owner.process(item)
    assert owner.snapshot == before
    result = owner.process(item)
    assert result.account.position.quantity == 1 and len(result.financial) == 1
    assert result.account.fees_paid == D("2.3")


@pytest.mark.parametrize("seam", ["second_child", "event", "account_publication", "session_publication"])
def test_creation_failure_rolls_back_both_children(monkeypatch,seam):
    import quantlab.paper.oco as oco
    from quantlab.paper.orders import PaperOrderKernel
    from quantlab.paper.accounts import PaperAccount
    owner, _ = setup(); execute(owner); item = create(owner); before = owner.snapshot
    def fail(*args,**kw): raise KeyboardInterrupt("creation")
    with monkeypatch.context() as patch:
        if seam == "second_child":
            original = PaperOrderKernel._prepare
            def prepare(self,item,**kw):
                if getattr(self._config,"child_role",None) == "take_profit": fail()
                return original(self,item,**kw)
            patch.setattr(PaperOrderKernel,"_prepare",prepare)
        elif seam == "event": patch.setattr(oco,"_event",fail)
        elif seam == "account_publication": patch.setattr(PaperAccount,"_publish",fail)
        else: patch.setattr(owner,"_publish_candidate",fail)
        with pytest.raises(KeyboardInterrupt): owner.process(item)
    assert owner.snapshot == before and len(owner._publication.account._kernels) == 1
    owner.process(item)
    assert len(owner._publication.account._kernels) == 3


def test_session_stop_cancels_both_children_without_liquidation():
    owner, _ = setup(); execute(owner); owner.process(create(owner)); owner.process(q(7,"110"))
    out = owner.stop(command("stop",8,T+3*MINUTE))
    assert out.oco.state == "cancelled" and len(out.oco_events) == 2
    assert out.oco.stop.state is out.oco.target.state is S.CANCELLED
    assert out.account.position.quantity == 1 and out.account.fees_paid == D("2.3")


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("target", [False, True])
def test_owned_long_short_prices_and_slippage(short,target):
    from tests.paper.test_accounts import opened
    from tests.paper.helpers import START, SECOND
    from tests.paper.test_advanced import quote
    from quantlab.backtesting import ExecutionCostConfig
    account = opened(short=short)
    p = account.snapshot.position
    cmd = OCOCommand(command_id="protect",sequence=3,timestamp=START,
        action="submit_oco",reason_reference="human",account_id=p.account_id,strategy_id=p.strategy_id,
        instrument_id=p.instrument.instrument_id,position_id=p.entry_transaction_id,quantity=p.quantity,
        stop_price=D("106" if short else "96"),target_price=D("95" if short else "105"))
    created=account.create_protective_oco(cmd,costs=ExecutionCostConfig(slippage=D("2")),maximum_age=100*SECOND)
    gid=created.group.group_id
    if not target:
        account.process_oco(gid,quote(4,bid="108" if short else "95",ask="110" if short else "97"))
        result=account.process_oco(gid,quote(5,bid="110" if short else "94",ask="112" if short else "96"))
        expected=D("114" if short else "92")
    else:
        result=account.process_oco(gid,quote(4,bid="92" if short else "106",ask="94" if short else "108"))
        expected=D("95" if short else "105")  # Slippage capped at target.
    fill=next(e for e in (*result.stop_records,*result.target_records) if isinstance(e,OCOFillRecord))
    assert fill.execution.execution_price == expected
    assert result.group.state == "closed" and account.snapshot.position.quantity == 0
    assert account.snapshot.realized_pnl == (D("100")-expected if short else expected-D("102"))*2
    assert account.snapshot.fees_paid == D("1.2")  # Exit cost policy has no explicit fees.


@pytest.mark.parametrize("length",[0,8,64,200])
@pytest.mark.parametrize("operation,paths",[("resting",5),("trigger",6),("partial",12),("complete",16),("cancel",5)])
def test_active_oco_work_is_bounded_at_all_retained_lengths(monkeypatch,length,operation,paths):
    from quantlab.paper import PaperAccount
    from tests.paper.test_accounts import cfg,reserve,settlement
    from tests.paper.helpers import START,SECOND
    from tests.paper.test_advanced import quote
    from tests.paper.test_history_hot_path import protect_prefix
    account=PaperAccount(cfg(starting_capital=D("50000")))
    account.apply_trusted(reserve(amount="25526"))
    account.apply_trusted(settlement(quantity="250"))
    p=account.snapshot.position
    cmd=OCOCommand(command_id="protect",sequence=3,timestamp=START,action="submit_oco",reason_reference="human",
        account_id=p.account_id,strategy_id=p.strategy_id,instrument_id=p.instrument.instrument_id,
        position_id=p.entry_transaction_id,quantity=p.quantity,stop_price=D("96"),target_price=D("105"))
    created=account.create_protective_oco(cmd,liquidity_per_observation=None if operation == "complete" else D("1"),
        maximum_age=1000*SECOND,maximum_inputs=256)
    gid=created.group.group_id
    for n in range(4,4+length): account.process_oco(gid,quote(n))
    before=account.oco_snapshot
    n=4+length
    if operation == "cancel":
        item=OCOCommand(command_id="cancel",sequence=n,timestamp=START+n*SECOND,action="request_cancel_oco",
            reason_reference="human",account_id=p.account_id,strategy_id=p.strategy_id,
            instrument_id=p.instrument.instrument_id,position_id=p.entry_transaction_id,group_id=gid)
    else: item=quote(n,bid="95" if operation == "trigger" else "110" if operation in ("partial","complete") else "100",
        ask="112" if operation in ("partial","complete") else "102")
    with monkeypatch.context() as patch:
        copies=protect_prefix(patch)
        outcome=account.process_oco(gid,item)
        count=len(copies)
        assert account.process_oco(gid,item) is outcome
        assert len(copies) == count == paths*64 and max(copies)<=16
    assert before.group.remaining_quantity == 250
    after=account.oco_snapshot
    assert after.stop.events[:len(before.stop.events)] == before.stop.events
    assert after.target.events[:len(before.target.events)] == before.target.events
    assert account.snapshot.position.quantity == (0 if operation == "complete" else 249 if operation == "partial" else 250)


def test_financial_failure_aborts_whole_group_and_remains_cancellable(monkeypatch):
    from quantlab.paper.accounts import PaperAccount
    owner,_=setup(); execute(owner); owner.process(create(owner)); before=owner.snapshot
    def deny(*args): raise PaperInputError("unsupported financial preparation")
    with monkeypatch.context() as patch:
        patch.setattr(PaperAccount,"_prepare_apply",deny)
        with pytest.raises(PaperInputError): owner.process(q(7,"110"))
    assert owner.snapshot == before
    owner.process(cancel(owner,7)); out=owner.process(cancel(owner,8,"ack_cancel_oco"))
    assert out.oco.state == "cancelled" and out.account.position.quantity == 2


@pytest.mark.parametrize("phase",["v1","unapproved","paused"])
def test_oco_cannot_bypass_session_admission_or_lifecycle(phase):
    from tests.paper.test_sessions import session
    template,_=setup(); execute(template); cmd=create(template)
    if phase == "v1":
        owner,_=session(); execute(owner)
    else:
        owner,_=setup()
        if phase == "unapproved": owner.start(command("start"))
        else:
            execute(owner); owner.pause(command("pause",6,T+MINUTE))
            cmd=cmd.model_copy(update={"sequence":7,"command_id":"oco-paused"})
    before=owner.snapshot
    with pytest.raises(PaperInputError): owner.process(cmd)
    assert owner.snapshot == before


def test_active_position_rejects_uncoordinated_reduction_and_accepts_exact_financial_retry():
    from quantlab.paper.account_models import OCOApplyFill
    from quantlab.persistence.contracts import decode
    from tests.paper.test_accounts import settlement
    from quantlab.backtesting import SignalAction
    owner,_=setup(); execute(owner); owner.process(create(owner))
    account=owner._publication.account
    before=owner.snapshot
    unowned=settlement(n=20,action=SignalAction.EXIT_LONG,quantity="1",bid="110",ask="112",time=T+2*MINUTE)
    unowned=unowned.model_copy(update={"account_id":account.snapshot.config.account_id,
        "strategy_id":account.snapshot.position.strategy_id,"source":q(7,"110").observation})
    with pytest.raises(PaperInputError,match="active OCO position"): account.apply_trusted(unowned)
    assert owner.snapshot == before
    out=owner.process(q(7,"110")); account=owner._publication.account
    input=decode(OCOApplyFill,account.get_input_record(out.financial[0].input_id))
    assert account.apply_trusted(input) == out.financial[0]
    assert owner.snapshot.account.fees_paid == D("2.3")


def test_cancelled_group_cannot_be_recreated_or_amended_on_same_position():
    owner,_=setup(); execute(owner); owner.process(create(owner))
    owner.process(cancel(owner,7)); owner.process(cancel(owner,8,"ack_cancel_oco")); before=owner.snapshot
    with pytest.raises(PaperInputError): owner.process(create(owner,n=9,timestamp=T+3*MINUTE))
    bad=cancel(owner,9).model_copy(update={"target_price":D("106")})
    with pytest.raises(PaperInputError): owner.process(bad)
    assert owner.snapshot == before


def test_capacity_still_permits_atomic_cancellation_completion():
    from tests.paper.test_accounts import opened
    from tests.paper.helpers import START,SECOND
    from tests.paper.test_advanced import quote
    a=opened(); p=a.snapshot.position
    cmd=OCOCommand(command_id="oco",sequence=3,timestamp=START,action="submit_oco",reason_reference="human",
        account_id=p.account_id,strategy_id=p.strategy_id,instrument_id=p.instrument.instrument_id,
        position_id=p.entry_transaction_id,quantity=p.quantity,stop_price=D("96"),target_price=D("105"))
    created=a.create_protective_oco(cmd,maximum_inputs=3,maximum_age=100*SECOND)
    h=created.group
    a.process_oco(h.group_id,quote(4))
    before=a.oco_snapshot
    with pytest.raises(PaperInputError): a.process_oco(h.group_id,quote(5))
    assert a.oco_snapshot == before
    for n,action in ((5,"request_cancel_oco"),(6,"ack_cancel_oco")):
        a.process_oco(h.group_id,OCOCommand(command_id=f"cancel-{n}",sequence=n,timestamp=START+6*SECOND,
            action=action,reason_reference="human",account_id=h.account_id,strategy_id=h.strategy_id,
            instrument_id=h.instrument_id,position_id=h.position_id,group_id=h.group_id))
    assert a.oco_snapshot.group.state == "cancelled" and a.snapshot.position.quantity == 2


@pytest.mark.parametrize("field",["history_digest","event_count","quantity","costs"])
def test_immutable_public_snapshot_rejects_inconsistent_group_head(field):
    from quantlab.paper import OCOSnapshot
    owner,_=setup(); execute(owner); owner.process(create(owner)); owner.process(q(7,"110"))
    saved=owner.snapshot.oco; wire=saved.canonical_json(); h=saved.group
    if field == "history_digest":
        h=h.model_copy(update={"target":h.target.model_copy(update={"history_digest":"f"*64})})
    elif field == "event_count": h=h.model_copy(update={"target":h.target.model_copy(update={"event_count":h.target.event_count+1})})
    elif field == "quantity":
        h=h.model_copy(update={"remaining_quantity":D("1.5"),"target":h.target.model_copy(update={"filled_quantity":D("0.5")}),
            "stop":h.stop.model_copy(update={"withdrawn_quantity":D("0.5")})})
    else:
        h=h.model_copy(update={"target":h.target.model_copy(update={"cumulative_costs":h.target.cumulative_costs.model_copy(update={"fees":D("12")})})})
    with pytest.raises(ValueError): OCOSnapshot(group=h,stop=saved.stop,target=saved.target)
    owner.process(q(8,"111"))
    assert saved.canonical_json() == wire and saved.group.remaining_quantity == 1


def test_creation_interruption_after_account_publication_revokes_root_and_bindings(monkeypatch):
    from tests.paper.test_accounts import opened
    from tests.paper.helpers import START
    a=opened(); before=a.snapshot; events=a.events; registry=dict(a._kernels)
    p=before.position
    cmd=OCOCommand(command_id="handoff",sequence=3,timestamp=START,action="submit_oco",reason_reference="human",
        account_id=p.account_id,strategy_id=p.strategy_id,instrument_id=p.instrument.instrument_id,
        position_id=p.entry_transaction_id,quantity=p.quantity,stop_price=D("96"),target_price=D("105"))
    original=a._publish
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        assert a.oco_progress is not None
        raise KeyboardInterrupt("after account publication")
    with monkeypatch.context() as patch:
        patch.setattr(a,"_publish",interrupted)
        with pytest.raises(KeyboardInterrupt): a.create_protective_oco(cmd)
    assert a.snapshot is before and a.events == events and a.oco_progress is None
    assert a._kernels == registry and not a._publication.orders
    created=a.create_protective_oco(cmd)
    assert created.group.state == "active" and len(a._kernels) == 2
    assert a.snapshot is before and a.events == events


def test_session_stop_completes_existing_pending_cancel_at_kernel_capacity(monkeypatch):
    from quantlab.paper.accounts import PaperAccount
    owner,_=setup(); execute(owner)
    original=PaperAccount.create_protective_oco
    def bounded(self,command,**kwargs): return original(self,command,maximum_inputs=3,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(PaperAccount,"create_protective_oco",bounded)
        owner.process(create(owner))
    owner.process(q(7,"100"))
    owner.process(cancel(owner,8))
    before=owner.snapshot
    assert before.oco.group.stop.input_count == 4 and before.oco.group.state == "cancel_pending"
    out=owner.stop(command("stop",9,T+4*MINUTE))
    assert out.oco.state == "cancelled" and len(out.oco_events) == 1
    assert out.oco.stop.input_count == out.oco.target.input_count == 5
    assert out.account == before.account and out.account.position.quantity == 2
    assert out.oco.stop.state is out.oco.target.state is S.CANCELLED
    assert owner.stop(out.command) is out
