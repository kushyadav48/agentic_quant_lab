"""Structural scaling checks: no retained-prefix traversal on order processing."""
from decimal import Decimal as D
import builtins

import pytest

from quantlab.paper import PaperOrderKernel, KernelSnapshot, OrderState as S
from quantlab.paper.history import History, RetainedMap
import quantlab.paper.history as history
from tests.paper.test_advanced import kernel, quote, owned_entry, ack
from tests.paper.helpers import config, market, submission, cancel, SECOND


def forbidden(*args, **kwargs):
    raise AssertionError("processing traversed retained history")


def protect_prefix(monkeypatch):
    monkeypatch.setattr(History, "__iter__", forbidden)
    monkeypatch.setattr(RetainedMap, "__iter__", forbidden)
    monkeypatch.setattr(KernelSnapshot, "__init__", forbidden)
    copies = []
    def counted(branch):
        copies.append(len(branch))
        return builtins.dict(branch)
    monkeypatch.setattr(history, "dict", counted, raising=False)
    return copies


@pytest.mark.parametrize("length", [0, 8, 64, 200])
@pytest.mark.parametrize("kind", ["partial", "resting", "legacy_terminal"])
def test_order_processing_copies_only_bounded_index_nodes(monkeypatch, length, kind):
    if kind == "legacy_terminal":
        k = PaperOrderKernel(config())
        k.process(market()); k.process(submission()); k.process(quote(3))
        start = 4
    else:
        kwargs = dict(maximum_inputs=256, maximum_age=1000*SECOND)
        if kind == "partial":
            kwargs.update(budget="1", quantity="250")
        else:
            kwargs.update(order_type="limit", limit_price=D("99"))
        k = kernel(**kwargs)
        start = 3
    for n in range(start, start+length):
        k.process(quote(n))
    before = k._view
    item = quote(start+length)
    with monkeypatch.context() as patch:
        copies = protect_prefix(patch)
        records = k.process(item)
        assert k.process(item) is records  # Exact retry touches no index path.
    after = k._view
    assert after.inputs.previous is before.inputs
    if records:
        assert after.events.previous is before.events
    else:
        assert after.events is before.events
    # One retry index + one market index + event IDs + one liquidity index per fill.
    updates = 2 + len(records) + int(kind == "partial")
    assert len(copies) == 64 * updates
    assert max(copies) <= 16
    assert sum(copies) <= 16 * 64 * updates
    saved = k.snapshot
    assert len(saved.inputs) == len(after.inputs)
    assert KernelSnapshot.model_validate(saved) == saved
    assert saved.filled_quantity == (D(length+1) if kind == "partial" else D("2") if kind == "legacy_terminal" else D("0"))


@pytest.mark.parametrize("length", [0, 32, 100])
def test_account_partial_fill_denial_and_cancel_do_not_materialize_history(monkeypatch, length):
    account, k = owned_entry(quantity="120", capital="20000")
    for n in range(3, 3+length):
        k.process(quote(n))
    snapshot = k.snapshot
    request = cancel(k, sequence=length+3, timestamp=k._view.timestamp)
    with monkeypatch.context() as patch:
        protect_prefix(patch)
        k.process(request)
    acknowledgment = ack(k, length+4)
    with monkeypatch.context() as patch:
        protect_prefix(patch)
        k.process(acknowledgment)
    assert k.snapshot.state is S.CANCELLED
    assert k.snapshot.filled_quantity == snapshot.filled_quantity
    assert not account.snapshot.reservations
    assert snapshot.state is (S.PARTIALLY_FILLED if length else S.ACCEPTED)


def test_financial_denial_keeps_prefix_without_copy(monkeypatch):
    account, k = owned_entry(capital="210")
    k.process(quote(3))
    before = k._view
    with monkeypatch.context() as patch:
        protect_prefix(patch)
        k.process(quote(4, ask="200"))
    assert k._view.events.previous is before.events
    assert k.snapshot.state is S.CANCELLED
    assert k.snapshot.filled_quantity == 1
    assert account.snapshot.position.quantity == 1


def test_public_snapshot_identity_and_prefix_remain_frozen():
    k = kernel(budget="1", quantity="3")
    before = k.snapshot
    wire = before.canonical_json()
    k.process(quote(3)); k.process(quote(4))
    assert before.canonical_json() == wire
    assert len(before.inputs) == 2 and before.filled_quantity == 0
    assert k.snapshot is k.snapshot
    assert k.snapshot.events[:len(before.events)] == before.events


@pytest.mark.parametrize("length", [0, 32, 100])
def test_reservation_and_terminal_adapters_use_cached_event_references(monkeypatch, length):
    from quantlab.paper import PaperAccount, CancellationRequest, CancellationAcknowledgement
    from tests.paper.test_accounts import cfg
    from tests.paper.test_advanced import advanced_command
    from tests.paper.helpers import START
    a = PaperAccount(cfg())
    k = a.create_advanced_order_kernel(session_id="adapter", strategy_id="alpha")
    k.process(market()); k.process(advanced_command(order_type="limit", limit_price=D("99")))
    for n in range(3, 3+length):
        k.process(CancellationRequest(command_id=f"pending-{n}", sequence=n, timestamp=START,
            causation_id="submit-1", order_id=k._view.order_id))
    with monkeypatch.context() as patch:
        protect_prefix(patch)
        a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    request = CancellationRequest(command_id="cancel-last", sequence=length+3, timestamp=START,
        causation_id="submit-1", order_id=k._view.order_id)
    k.process(request)
    k.process(CancellationAcknowledgement(command_id="ack-last", sequence=length+4, timestamp=START,
        causation_id=k._view.pending_cancellation.request_id, order_id=k._view.order_id))
    with monkeypatch.context() as patch:
        protect_prefix(patch)
        a.release_order(k, event_id="release-ack", sequence=3, timestamp=START)
    assert not a.snapshot.reservations and k.snapshot.state is S.CANCELLED
