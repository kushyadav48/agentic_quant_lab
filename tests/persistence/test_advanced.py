"""V2 position-linked exits reuse the Phase 18E durability uncertainty boundary."""
from decimal import Decimal as D
from datetime import timedelta
import json

import pytest

from quantlab.paper import (AdvancedReplayConfig, AdvancedSessionCommand, PaperSession,
    MarketDelivery, OrderState as S, PaperInputError, PaperIdentityConflict, stable_id)
from quantlab.persistence import DurablePaperSession, SQLitePaperStore, StoragePolicy, RecoveryRequired, PersistenceError
from quantlab.persistence.contracts import decode, Effects
from tests.paper.test_sessions import session, inputs, command, event, T, control
from tests.paper.strategy_helpers import close_quote, delivery
from tests.backtesting.helpers import MINUTE, CONFIG
from quantlab.backtesting import ExecutionCostConfig

COSTS = ExecutionCostConfig(commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1"))


def setup(path=None, budget=D("1"), interval=2):
    reference, owners = session(age=10*MINUTE, research_config=CONFIG.model_copy(update={"quantity": D("2"), "execution_costs": COSTS}))
    config = AdvancedReplayConfig(**reference.config.model_dump(exclude={"schema_version"}),
        liquidity_per_observation=budget)
    reference = PaperSession(config, **owners)
    policy = StoragePolicy(checkpoint_interval=interval)
    if path is None:
        return reference, owners
    store = SQLitePaperStore(path)
    return DurablePaperSession(store, config, storage_policy=policy, **owners), store, reference, owners, policy


def exit_command(owner, n=6, **changes):
    body = dict(command_id=f"exit-{n}", sequence=n, timestamp=T+MINUTE,
        action="submit_exit", reason_reference="human:protect-position",
        position_id=owner.snapshot.account.position.entry_transaction_id, quantity=D("2"))
    body.update(changes)
    return AdvancedSessionCommand(**body)


def q(n, price="110", time=None, *, age=None):
    time = T + MINUTE + (n-6)*MINUTE if time is None else time
    obs = close_quote(delivery(sequence=n), sequence=n)
    observed = time if age is None else time-age
    quote = obs.quote.model_copy(update={"timestamp": observed, "available_at": observed,
        "bid": D(price), "ask": D(price)+D("2")})
    return event(MarketDelivery(event_id=f"exit-quote-{n}", sequence=n,
        timestamp=time, delivered_at=time, quote=quote))


def execute(owner):
    for item in inputs():
        owner.process(item)
    assert owner.snapshot.account.position.quantity == 2


def reopened(path, config, owners, policy, checkpoint):
    store = SQLitePaperStore(path)
    try:
        owner = DurablePaperSession.recover(store, config, storage_policy=policy,
            use_checkpoint=checkpoint, **owners)
    except BaseException:
        store.close()
        raise
    return owner, store


@pytest.mark.parametrize("kind,prices,params", [
    ("market", ["110", "111"], {}),
    ("limit", ["99", "110", "111"], {"limit_price": D("105"), "protective_role": "take_profit"}),
    ("stop_market", ["95", "94", "93"], {"stop_price": D("96"), "protective_role": "stop_loss"}),
    ("stop_limit", ["95", "94", "98", "99"], {"stop_price": D("96"), "limit_price": D("97")}),
])
@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("prefix", [1, 2, 3, 4, 5])
def test_partial_trigger_and_final_exit_recovery(tmp_path, kind, prices, params, checkpoint, prefix):
    path = tmp_path / "paper.db"
    owner, store, reference, owners, policy = setup(path)
    execute(owner); execute(reference)
    item = exit_command(owner, order_type=kind, **params)
    sequence = [item, *(q(n, price) for n, price in enumerate(prices, 7))]
    for item in sequence[:prefix]:
        assert owner.process(item) == reference.process(item)
    before = owner.snapshot
    store.close()
    owner, store = reopened(path, reference.config, owners, policy, checkpoint)
    assert owner.snapshot == before == reference.snapshot
    assert owner.operator_required
    for retry in sequence[:prefix]:
        assert owner.process(retry) == reference.process(retry)
    assert owner.operator_required
    if prefix >= len(sequence):
        assert owner.snapshot.account.position.quantity == 0
        assert owner.snapshot.exit_order.state is S.FILLED
        assert owner.snapshot.account.fees_paid == D("3.4")
    store.close()


@pytest.mark.parametrize("prefix", [1, 2, 3, 4])
@pytest.mark.parametrize("checkpoint", [False, True])
def test_recover_each_protective_prefix(tmp_path, prefix, checkpoint):
    path = tmp_path / "prefix.db"
    owner, store, reference, owners, policy = setup(path)
    execute(owner); execute(reference)
    cmd = exit_command(owner, order_type="stop_market", stop_price=D("96"), protective_role="stop_loss")
    items = [cmd, q(7, "95"), q(8, "94"), q(9, "93")]
    for item in items[:prefix]:
        assert owner.process(item) == reference.process(item)
    before = owner.snapshot
    owner.checkpoint()
    store.close()
    owner, store = reopened(path, reference.config, owners, policy, checkpoint)
    assert owner.snapshot == before == reference.snapshot
    for item in items[:prefix]:
        owner.process(item)
    assert owner.snapshot == before and owner.operator_required
    if prefix == 3:
        assert before.exit_order.state is S.PARTIALLY_FILLED and before.account.position.quantity == 1
        assert before.account.fees_paid == D("2.3")
    elif prefix == 4:
        assert before.exit_order.state is S.FILLED and before.account.position.quantity == 0
        assert before.account.realized_pnl == D("-15")
        assert before.account.fees_paid == D("3.4")
        assert before.account.available_funds == D("981.6")
    store.close()


@pytest.mark.parametrize("checkpoint", [False, True])
def test_pending_cancel_recovery_race_and_operator_gate(tmp_path, checkpoint):
    path = tmp_path / "cancel.db"
    owner, store, reference, owners, policy = setup(path)
    execute(owner)
    cmd = exit_command(owner)
    owner.process(cmd); owner.process(q(7))
    req = AdvancedSessionCommand(command_id="cancel", sequence=8, timestamp=T+3*MINUTE,
        action="request_cancel_exit", position_id=cmd.position_id, reason_reference="human:cancel")
    owner.process(req)
    before = owner.snapshot
    owner.checkpoint(); store.close()
    owner, store = reopened(path, owner.config, owners, policy, checkpoint)
    assert owner.snapshot == before and owner.snapshot.exit_order.pending_cancellation is not None
    with pytest.raises(RecoveryRequired, match="operator_pause_required"):
        owner.process(q(9))
    for c in [command("pause", 9, T+3*MINUTE), command("resume", 10, T+3*MINUTE)]:
        owner.process(c)
    ack = AdvancedSessionCommand(command_id="ack", sequence=11, timestamp=T+3*MINUTE,
        action="ack_cancel_exit", position_id=cmd.position_id, reason_reference="human:ack")
    receipt = owner.process(ack)
    assert owner.snapshot.exit_order.state is S.CANCELLED and owner.snapshot.account.position.quantity == 1
    assert owner.snapshot.account.fees_paid == D("2.3")
    assert owner.process(ack) == receipt
    before = owner.snapshot
    store.close()
    owner, store = reopened(path, owner.config, owners, policy, checkpoint)
    assert owner.snapshot == before
    store.close()


@pytest.mark.parametrize("stage", ["before_commit", "after_commit", "publication"])
@pytest.mark.parametrize("operation", ["submit", "trigger", "partial", "final", "cancel", "ack"])
def test_advanced_fault_window_atomicity(tmp_path, monkeypatch, stage, operation):
    path = tmp_path / "fault.db"
    owner, store, reference, owners, policy = setup(path)
    execute(owner); execute(reference)
    cmd = exit_command(owner, order_type="stop_market", stop_price=D("96"), protective_role="stop_loss")
    req = AdvancedSessionCommand(command_id="cancel", sequence=9, timestamp=T+3*MINUTE,
        action="request_cancel_exit", position_id=cmd.position_id, reason_reference="cancel")
    ack = AdvancedSessionCommand(command_id="ack", sequence=10, timestamp=T+3*MINUTE,
        action="ack_cancel_exit", position_id=cmd.position_id, reason_reference="ack")
    items = [cmd, q(7,"95"), q(8,"94")]
    index = {"submit":0, "trigger":1, "partial":2, "final":3, "cancel":3, "ack":4}[operation]
    items += [q(9,"93")] if operation == "final" else [req, ack]
    for item in items[:index]:
        owner.process(item); reference.process(item)
    target = items[index]
    before = owner.snapshot
    with monkeypatch.context() as patch:
        if stage == "publication":
            def fail(candidate):
                raise KeyboardInterrupt("publication")
            patch.setattr(owner, "_publish_candidate", fail)
        else:
            def fail(actual):
                if actual == stage:
                    raise KeyboardInterrupt(stage)
            patch.setattr(store, "_fault", fail)
        with pytest.raises(KeyboardInterrupt if stage == "before_commit" else RecoveryRequired):
            owner.process(target)
    if stage == "before_commit":
        assert owner.snapshot == before and not owner.recovery_required
        owner.process(target)
    else:
        assert owner.recovery_required
        with pytest.raises(RecoveryRequired):
            _ = owner.snapshot
    reference.process(target)
    store.close()
    restored, store = reopened(path, reference.config, owners, policy, True)
    assert restored.snapshot == reference.snapshot
    assert restored.process(target) == reference.process(target)
    assert restored.snapshot.account.fees_paid == reference.snapshot.account.fees_paid
    store.close()


def test_protective_sibling_rejection_stale_pause_and_stop():
    owner, _ = setup()
    execute(owner)
    first = exit_command(owner, order_type="limit", limit_price=D("120"), protective_role="take_profit")
    owner.process(first)
    before = owner.snapshot
    with pytest.raises(PaperInputError, match="one active"):
        owner.process(exit_command(owner, n=7, order_type="stop_market", stop_price=D("90"), protective_role="stop_loss"))
    assert owner.snapshot == before
    owner.process(q(7,"130", time=T+12*MINUTE, age=11*MINUTE))
    assert owner.snapshot.exit_order.filled_quantity == 0
    owner.process(command("pause",8,T+12*MINUTE))
    owner.process(q(9,"130",time=T+13*MINUTE))
    assert owner.snapshot.exit_order.filled_quantity == 0
    owner.stop(command("stop",10,T+14*MINUTE))
    assert owner.snapshot.exit_order.state is S.CANCELLED and owner.snapshot.account.position.quantity == 2


def test_v1_session_rejects_exit_and_no_approval_bypass():
    owner, _ = session()
    execute(owner)
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(exit_command(owner))
    assert owner.snapshot == before
    owner, _ = setup()
    owner.start(command("start"))
    with pytest.raises(PaperInputError):
        owner.process(AdvancedSessionCommand(command_id="exit", sequence=2, timestamp=T,
            action="submit_exit", reason_reference="human", position_id="fake", quantity=D("1")))


@pytest.mark.parametrize("checkpoint", [False, True])
def test_exact_phase18e_frozen_journal_compatibility(tmp_path, checkpoint):
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent/"fixtures"/"phase18e-v1.json").read_text(encoding="utf-8"))
    assert fixture["baseline"] == "504606b3c47dab589b56ebc4a6707492048bbf3f"
    store = SQLitePaperStore(tmp_path/"original.db")
    connection = store._connection
    for table in ("metadata", "operations", "checkpoints"):
        for row in fixture[table]:
            placeholders = ",".join("?" for _ in row)
            connection.execute(f"INSERT INTO {table} VALUES ({placeholders})", row)
    reference, owners = session()
    for item in inputs():
        reference.process(item)
    owner = DurablePaperSession.recover(store, reference.config,
        storage_policy=StoragePolicy(checkpoint_interval=2), use_checkpoint=checkpoint, **owners)
    assert owner.snapshot == reference.snapshot
    assert owner.snapshot.account.fees_paid == 0
    for row in fixture["operations"]:
        retained = store.lookup(row[1])
        assert retained.canonical_json() == row[-1]
    assert [c.canonical_json() for c in store.checkpoints()] == [row[-1] for row in fixture["checkpoints"]]
    store.close()


@pytest.mark.parametrize("checkpoint", [False, True])
def test_ioc_partial_exit_expiry_recovery(tmp_path, checkpoint):
    path = tmp_path/"ioc.db"
    owner, store, reference, owners, policy = setup(path)
    execute(owner)
    cmd = exit_command(owner, time_in_force="ioc")
    owner.process(cmd)
    owner.process(q(7,"110"))
    assert owner.snapshot.exit_order.state is S.EXPIRED
    assert owner.snapshot.account.position.quantity == 1
    assert owner.snapshot.account.realized_pnl == D("9")
    assert owner.snapshot.account.fees_paid == D("2.3")
    before = owner.snapshot
    owner.checkpoint(); store.close()
    owner, store = reopened(path, owner.config, owners, policy, checkpoint)
    assert owner.snapshot == before and owner.snapshot.exit_order.terminated
    store.close()


@pytest.mark.parametrize("checkpoint", [False, True])
def test_shared_budget_recovery_across_sequential_exit_children(tmp_path, checkpoint):
    path=tmp_path/"shared.db"
    owner,store,reference,owners,policy=setup(path)
    execute(owner)
    first=exit_command(owner,quantity=D("1"))
    owner.process(first)
    delivered=q(7,"110")
    owner.process(delivered)
    assert owner.snapshot.account.position.quantity == 1
    owner.checkpoint();store.close()
    owner,store=reopened(path,owner.config,owners,policy,checkpoint)
    owner.process(command("pause",8,T+2*MINUTE))
    owner.process(command("resume",9,T+2*MINUTE))
    second=exit_command(owner,n=10,quantity=D("1"),timestamp=T+2*MINUTE)
    owner.process(second)
    # Distinct delivery ID and sequence, identical recorded quote: budget remains spent.
    obs=delivered.observation.model_copy(update={"event_id":"redelivery", "sequence":11})
    owner.process(event(obs))
    assert owner.snapshot.account.position.quantity == 1
    owner.process(q(12,"110",time=T+3*MINUTE))
    assert owner.snapshot.account.position.quantity == 0
    store.close()


@pytest.mark.parametrize('pending_count,partial', [(128, False), (129, True), (4, True)])
def test_stop_completes_pending_single_exit_at_capacity_without_financial_effects(tmp_path, pending_count, partial):
    from tests.persistence.test_history_hot_path import large_session
    from quantlab.paper.models import PendingCancellation
    path = tmp_path / 'single-exit-capacity.db'
    owner, store, reference, owners, policy = large_session(path, interval=20)
    cmd = exit_command(owner, quantity=D('250'), order_type='limit', limit_price=D('105'))
    assert owner.process(cmd) == reference.process(cmd)
    for n in range(7, pending_count + 4):
        item = q(n, '110' if partial and n == 7 else '100')
        assert owner.process(item) == reference.process(item)
    request_sequence = pending_count + 4
    req = AdvancedSessionCommand(command_id='pending-single-exit', sequence=request_sequence,
        timestamp=T + (request_sequence - 5)*MINUTE, action='request_cancel_exit',
        position_id=cmd.position_id, reason_reference='human:cancel')
    assert owner.process(req) == reference.process(req)
    before = owner.snapshot
    assert len(before.exit_order.inputs) == pending_count
    assert before.exit_order.pending_cancellation.request_id == req.command_id
    assert before.exit_order.state is (S.PARTIALLY_FILLED if partial else S.ACCEPTED)
    stop = command('stop', request_sequence + 1, req.timestamp)
    try:
        result = owner.stop(stop)
        assert result == reference.stop(stop)
        assert result.exit_order.state is S.CANCELLED and not result.financial
        assert result.account == before.account
        assert result.account.position.quantity == (249 if partial else 250)
        assert result.exit_order.filled_quantity == (1 if partial else 0)
        # Below capacity, preserve previously valid v2 stop outputs and retry wires.
        extra_request = pending_count < before.exit_order.config.maximum_inputs
        assert sum(isinstance(e, PendingCancellation) for e in result.orders) == int(extra_request)
        assert result.exit_order.input_count == pending_count + 1 + int(extra_request)
        assert owner.stop(stop) is result
        owner.checkpoint()
        expected, config = owner.snapshot, owner.config
    finally:
        store.close()
    for checkpoint in (False, True):
        restored, recovered_store = reopened(path, config, owners, policy, checkpoint)
        try:
            assert restored.snapshot == expected == reference.snapshot
            assert not restored.operator_required
            assert restored.stop(stop) == result
        finally:
            recovered_store.close()
