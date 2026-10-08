"""Opening provenance/retry results commit with execution, settlement and release."""
from dataclasses import FrozenInstanceError

import pytest
from quantlab.backtesting import BacktestConfig, ExecutionCostConfig
from quantlab.paper import (
    FillRecord, OrderState, PaperAccount, PaperIdentityConflict, RiskOutcome, stable_id,
)
from tests.backtesting.helpers import D
from .strategy_helpers import close_quote, delivery, opening, ready


def prepared(cancel):
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(slippage=D("0.5"),
            commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1")))
    rt, adapter, account, bar, _, _ = ready(
        capital="300" if cancel else "1000", research_config=config)
    adapter.submit(close_quote(bar))
    return rt, adapter, account, opening(bar, price="200" if cancel else "101")


def retained(adapter, account):
    # Include public observations and every financial/kernel/opening retry index.
    return (account.snapshot, adapter.snapshot, account.events, adapter.audit_snapshot,
        dict(account._seen), tuple(account._journal), set(account._reservation_ids),
        set(account._reserved_orders), set(account._settled_orders),
        set(account._execution_ids), dict(account._adapter_seen),
        dict(adapter._kernel._current_state().seen), dict(account._publication.openings))


def verify_committed(adapter, account, op, records, cancel):
    assert adapter.openings == (op,)
    ack = account._opening_acknowledgement(adapter._kernel, op.event_id)
    assert ack.records is records
    assert ack.opening == op and ack.wire == op.canonical_json()
    assert ack.source == adapter.snapshot.inputs[-1] == adapter.snapshot.market
    assert ack.source.event_id == stable_id("paper-strategy-opening-v1", op)
    assert ack.source.quote == op.quote
    assert ack.source.sequence == op.sequence > adapter.snapshot.submission.sequence
    assert ack.source.timestamp == op.bar_start == adapter.snapshot.submission.timestamp
    assert all(r.timestamp == op.timestamp for r in records)
    assert next(r for r in records if isinstance(r, RiskOutcome)).source == ack.source
    assert len(account.events) == 2
    assert account.snapshot.reservations == ()
    if cancel:
        assert adapter.snapshot.state is OrderState.CANCELLED
        assert not any(isinstance(r, FillRecord) for r in records)
        assert account.snapshot.position is None and account.snapshot.fees_paid == 0
        assert account.events[-1].kind == "release"
        assert account.events[-1].causation_id == records[-1].event_id
    else:
        assert adapter.snapshot.state is OrderState.FILLED
        fill = next(r for r in records if isinstance(r, FillRecord))
        assert fill.source == ack.source
        assert account.snapshot.position is not None
        assert account.snapshot.position.valuation == ack.source
        assert account.snapshot.fees_paid == D("1.2")
        assert account.events[-1].kind == "settle"
        assert account.events[-1].causation_id == fill.event_id
    with pytest.raises(FrozenInstanceError):
        ack.wire = "changed"
    with pytest.raises(TypeError):
        account._publication.openings[("session", op.event_id)] = ack


@pytest.mark.parametrize("cancel", [False, True], ids=["charged-fill", "unfunded-cancellation"])
@pytest.mark.parametrize("failure", ["ack-allocation", "ack-serialization", "ack-index",
    "financial-index", "root-allocation", "before-swap", "after-swap"])
def test_opening_acknowledgement_failure_leaves_everything_unchanged(monkeypatch, cancel, failure):
    import quantlab.paper.accounts as module
    rt, adapter, account, op = prepared(cancel)
    before = retained(adapter, account)
    publication = account._publication
    runtime_before = rt.snapshot
    with monkeypatch.context() as patch:
        if failure == "ack-allocation":
            original = account._prepare_opening_acknowledgement
            def fail(*args):
                candidate = original(*args)
                assert candidate.opening == op
                assert candidate.records[-1].state is (OrderState.CANCELLED if cancel else OrderState.FILLED)
                assert retained(adapter, account) == before
                raise MemoryError("opening acknowledgement allocation")
            patch.setattr(account, "_prepare_opening_acknowledgement", fail)
        elif failure == "ack-serialization":
            original = module.canonical_json
            def fail(value):
                original(value)
                assert value[0] == op
                raise ValueError("opening acknowledgement serialization")
            patch.setattr(module, "canonical_json", fail)
        elif failure == "ack-index":
            original = module.MappingProxyType
            def fail(value):
                candidate = original(value)
                if ("session", op.event_id) in candidate:
                    assert candidate[("session", op.event_id)].opening == op
                    raise MemoryError("opening acknowledgement index")
                return candidate
            patch.setattr(module, "MappingProxyType", fail)
        elif failure == "financial-index":
            original = account._stage_indexes
            def fail(financial):
                original(financial)
                assert account.events == before[2]
                assert adapter.audit_snapshot == before[3]
                raise MemoryError("financial index staging")
            patch.setattr(account, "_stage_indexes", fail)
        elif failure == "root-allocation":
            original = module._AccountPublication
            def fail(*args):
                candidate = original(*args)
                assert candidate.openings[("session", op.event_id)].opening == op
                raise MemoryError("publication allocation")
            patch.setattr(module, "_AccountPublication", fail)
        else:
            original = PaperAccount.__setattr__
            def fail(owner, name, value):
                if owner is account and name == "_publication" and value is not publication:
                    # Every candidate already contains the complete acknowledgement.
                    assert value.openings[("session", op.event_id)].records
                    if failure == "after-swap":
                        original(owner, name, value)
                        assert adapter.openings == (op,)
                        assert account.snapshot.reservations == ()
                    raise MemoryError("publication pointer failure")
                original(owner, name, value)
            patch.setattr(PaperAccount, "__setattr__", fail)
        with pytest.raises((MemoryError, ValueError)):
            adapter.process_open(op)
    assert retained(adapter, account) == before
    assert account._publication is publication
    assert rt.snapshot == runtime_before
    assert not account._busy
    assert account.snapshot.position is None
    assert account.snapshot.fees_paid == 0
    assert len(account.snapshot.reservations) == 1
    records = adapter.process_open(op)
    verify_committed(adapter, account, op, records, cancel)
    after = retained(adapter, account)
    assert adapter.process_open(op) is records
    assert retained(adapter, account) == after
    with pytest.raises(PaperIdentityConflict):
        adapter.process_open(op.model_copy(update={"bar_end": op.bar_end + (op.bar_end - op.bar_start)}))
    assert retained(adapter, account) == after


@pytest.mark.parametrize("cancel", [False, True], ids=["charged-fill", "unfunded-cancellation"])
def test_opening_commit_replay_and_provenance_are_deterministic(cancel):
    def execute():
        rt, adapter, account, op = prepared(cancel)
        records = adapter.process_open(op)
        verify_committed(adapter, account, op, records, cancel)
        committed = retained(adapter, account)
        publication = account._publication
        rt.process(delivery(1))
        assert adapter.process_open(op) is records  # Replay precedes terminal/late guards.
        assert account._publication is publication
        assert retained(adapter, account) == committed
        return records, committed, adapter.audit_snapshot.canonical_json(), account.snapshot.canonical_json()
    assert execute() == execute()


@pytest.mark.parametrize("cancel", [False, True], ids=["charged-fill", "unfunded-cancellation"])
def test_opening_and_financial_results_become_visible_at_one_publication(monkeypatch, cancel):
    rt, adapter, account, op = prepared(cancel)
    before = retained(adapter, account)
    publication = account._publication
    original = account._stage_indexes
    def inspect(financial):
        original(financial)
        # Financial journal/index staging cannot expose a fill, release or opening.
        assert account._publication is publication
        assert account.snapshot == before[0] and adapter.snapshot == before[1]
        assert account.events == before[2] and adapter.audit_snapshot == before[3]
        assert adapter.openings == ()
    monkeypatch.setattr(account, "_stage_indexes", inspect)
    records = adapter.process_open(op)
    assert account._publication is not publication
    verify_committed(adapter, account, op, records, cancel)


@pytest.mark.parametrize("cancel", [False, True], ids=["charged-fill", "unfunded-cancellation"])
def test_existing_terminal_account_adapters_preserve_opening_acknowledgements(cancel):
    rt, adapter, account, op = prepared(cancel)
    records = adapter.process_open(op)
    snapshot, events = account.snapshot, account.events
    opening_index = account._publication.openings
    acknowledge = account.release_order if cancel else account.settle_order
    event = acknowledge(adapter._kernel, event_id="terminal-ack", sequence=3, timestamp=op.timestamp)
    assert event == events[-1]
    assert account.snapshot == snapshot and account.events == events
    assert account._publication.openings is opening_index
    assert adapter.process_open(op) is records
    verify_committed(adapter, account, op, records, cancel)
