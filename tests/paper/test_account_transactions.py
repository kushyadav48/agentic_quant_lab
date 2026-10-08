"""Cross-component atomicity, failure injection and deterministic replay."""
from decimal import Inexact, localcontext

import pytest

from quantlab.paper import (
    ApplyFill, FillRecord, MarketDelivery, OrderSide, OrderState, PaperAccount,
    PaperIdentityConflict, PaperInputError, PaperOrderKernel, ReleaseFunds,
)
from .helpers import D, START, SECOND, cancel, market, submission
from .test_accounts import COSTS, cfg, reserve, settlement


def owned(*, capital="1000", side=OrderSide.BUY, costs=COSTS, account=None,
          session="session", strategy="alpha", sequence=1):
    account = PaperAccount(cfg(starting_capital=D(capital))) if account is None else account
    kernel = account.create_order_kernel(session_id=session, strategy_id=strategy, costs=costs)
    kernel.process(market())
    kernel.process(submission(side=side))
    account.reserve_order(kernel, event_id=f"reserve-{session}", sequence=sequence, timestamp=START)
    return account, kernel


def retained(account, kernel):
    return (account._publication, account.snapshot, kernel.snapshot, account.events,
        dict(account._seen), tuple(account._journal), set(account._settled_orders),
        set(account._execution_ids), set(account._reservation_ids), set(account._reserved_orders),
        dict(account._adapter_seen), dict(kernel._current_state().seen))


def assert_no_fill(account, kernel, reason):
    assert kernel.snapshot.state is OrderState.CANCELLED
    assert kernel.snapshot.events[-1].reason == reason
    assert not any(isinstance(e, FillRecord) for e in kernel.snapshot.events)
    assert not any(e.kind == "settle" for e in account.events)
    assert account.snapshot.position is None
    assert account.snapshot.fees_paid == 0
    assert account.snapshot.reservations == ()
    assert account.snapshot.available_funds == account.snapshot.balance


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_unaffordable_gap_commits_only_cancellation_and_release(side):
    a, k = owned(capital="205.2", side=side)
    old_account, old_order = a.snapshot, k.snapshot
    quote = market(3, bid="110", ask="112")
    result = k.process(quote)
    assert_no_fill(a, k, "account_unfunded")
    assert a.events[-1].kind == "release"
    assert a.events[-1].causation_id == result[-1].event_id
    assert a.events[-1].transaction_id == k.snapshot.order_id
    assert a.snapshot.state_version == 2
    assert old_order.state is OrderState.ACCEPTED
    assert old_account.reserved_funds > 0
    final = retained(a, k)
    assert k.process(quote) is result
    assert retained(a, k) == final


@pytest.mark.parametrize("side,basis,collateral,equity", [
    (OrderSide.BUY, "102", "204", "994.8"),
    (OrderSide.SELL, "100", "200", "994.8"),
])
def test_success_publishes_fill_and_account_exactly_once(side, basis, collateral, equity):
    a, k = owned(side=side)
    result = k.process(market(3))
    fill = next(e for e in result if isinstance(e, FillRecord))
    assert k.snapshot.state is OrderState.FILLED
    applications = [e for e in a.events if e.kind == "settle"]
    assert len(applications) == 1
    event = applications[0]
    assert event.causation_id == fill.event_id and event.transaction_id == fill.order_id
    assert event.account_id == "account" and event.strategy_id == "alpha"
    assert a.snapshot.position.entry_basis == D(basis)
    assert a.snapshot.position_collateral == D(collateral)
    assert a.snapshot.balance == D("998.8") and a.snapshot.equity == D(equity)
    assert a.snapshot.fees_paid == D("1.2") and a.snapshot.position.quantity == 2
    assert a.snapshot.reservations == ()
    command = ApplyFill.model_validate_json(a.get_input_record(event.input_id))
    assert command.execution == fill.execution and command.source == fill.source
    assert command.causation_id == fill.event_id
    for _ in range(3):
        assert k.process(market(3)) is result
        assert a.apply_trusted(command) is event
    for name in ("first-ack", "second-ack"):
        assert a.settle_order(k, event_id=name, sequence=2, timestamp=START) is event
    assert len(a.events) == 2 and a.snapshot.state_version == 2
    assert a.snapshot.fees_paid == D("1.2")


def test_financial_validation_failure_after_pricing_is_a_non_fill(monkeypatch):
    import quantlab.paper.accounts as owners
    original = owners.transition_account
    a, k = owned()
    attempts = []
    def reject(state, item):
        if isinstance(item, ApplyFill):
            attempts.append(item)
            assert item.execution.execution_price == D("102")
            raise PaperInputError("injected settlement validation failure")
        return original(state, item)
    monkeypatch.setattr(owners, "transition_account", reject)
    k.process(market(3))
    assert len(attempts) == 1
    assert_no_fill(a, k, "account_rejected")
    assert a.events[-1].kind == "release"


@pytest.mark.parametrize("stage", ["transition", "financial_event", "kernel_denial", "indexes"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_injected_preparation_and_staging_failures_leave_everything_unchanged(monkeypatch, stage, failure):
    import quantlab.paper.accounts as owners
    a, k = owned(capital="205.2" if stage == "kernel_denial" else "1000")
    before = retained(a, k)
    quote = market(3, bid="110", ask="112") if stage == "kernel_denial" else market(3)
    if stage == "transition":
        original = owners.transition_account
        def fail(state, item):
            result = original(state, item)
            if isinstance(item, ApplyFill):
                raise failure("after accounting calculation")
            return result
        monkeypatch.setattr(owners, "transition_account", fail)
    elif stage == "financial_event":
        original = owners.AccountEvent.canonical_json
        def fail(event):
            result = original(event)
            if event.kind == "settle":
                raise failure("after event serialization")
            return result
        monkeypatch.setattr(owners.AccountEvent, "canonical_json", fail)
    elif stage == "kernel_denial":
        def fail(*args):
            raise failure("before cancellation preparation")
        monkeypatch.setattr(k, "_without_fill", fail)
    else:
        original = a._stage_indexes
        def fail(financial):
            original(financial)
            # Public reads cannot expose staged journals, inputs or either snapshot.
            assert a.snapshot is before[1] and k.snapshot is before[2]
            assert a.events == before[3]
            if financial:
                with pytest.raises(PaperInputError):
                    a.get_input_record(financial[0].item.event_id)
            raise failure("after financial cache staging")
        monkeypatch.setattr(a, "_stage_indexes", fail)
    with pytest.raises(failure):
        k.process(quote)
    assert retained(a, k) == before
    assert not a._busy
    monkeypatch.undo()
    result = k.process(quote)
    if stage == "kernel_denial":
        assert_no_fill(a, k, "account_unfunded")
    else:
        assert k.snapshot.state is OrderState.FILLED
        assert len([e for e in a.events if e.kind == "settle"]) == 1
    assert k.process(quote) is result


@pytest.mark.parametrize("funding_denial", [False, True])
def test_failed_reservation_release_cannot_publish_a_terminal_order(monkeypatch, funding_denial):
    import quantlab.paper.accounts as owners
    a, k = owned(capital="205.2")
    before = retained(a, k)
    item = market(3, bid="110", ask="112") if funding_denial else cancel(k)
    original = owners.transition_account
    def fail(state, command):
        if isinstance(command, ReleaseFunds):
            raise PaperInputError("release validation failed")
        return original(state, command)
    monkeypatch.setattr(owners, "transition_account", fail)
    with pytest.raises(PaperInputError):
        k.process(item)
    assert retained(a, k) == before
    assert k.snapshot.state is OrderState.ACCEPTED and a.snapshot.reserved_funds == D("205.2")
    monkeypatch.undo()
    k.process(item)
    assert k.snapshot.state is OrderState.CANCELLED and a.snapshot.reservations == ()


def test_explicit_cancellation_and_terminal_acknowledgments_are_consistent():
    a, k = owned()
    request = cancel(k)
    result = k.process(request)
    assert k.snapshot.state is OrderState.CANCELLED
    assert a.snapshot.available_funds == 1000 and a.snapshot.reservations == ()
    event = a.events[-1]
    assert event.kind == "release"
    assert event.causation_id == next(e for e in result if getattr(e, "state", None)
        is OrderState.CANCELLED).event_id
    assert k.process(request) is result
    assert a.release_order(k, event_id="release-ack", sequence=2, timestamp=START) is event
    assert len(a.events) == 2


@pytest.mark.parametrize("funded", [False, True])
def test_conflicting_market_identity_cannot_change_either_component(funded):
    a, k = owned(capital="1000" if funded else "205.2")
    quote = market(3, bid="110", ask="112")
    k.process(quote)
    before = retained(a, k)
    conflict = market(3, bid="120", ask="122")
    with pytest.raises(PaperIdentityConflict):
        k.process(conflict)
    assert retained(a, k) == before


def test_conflicting_financial_identity_does_not_turn_into_a_fill_or_cancellation():
    a, k = owned()
    quote = market(3)
    proposal = k._prepare(quote)
    fill = next(e for e in proposal.records if isinstance(e, FillRecord))
    identity = a._execution_input(proposal.after.snapshot, fill).event_id
    other = reserve(2, event_id=identity, amount="10", rid="other", order="other")
    a.apply_trusted(other)
    before = retained(a, k)
    with pytest.raises(PaperIdentityConflict):
        k.process(quote)
    assert retained(a, k) == before
    assert k.snapshot.state is OrderState.ACCEPTED


def test_account_owned_kernel_cannot_publish_or_account_a_fill_separately():
    a, k = owned()
    proposal = k._prepare(market(3))
    fill = next(e for e in proposal.records if isinstance(e, FillRecord))
    before = retained(a, k)
    with pytest.raises(PaperInputError):
        k._publish_standalone(proposal)
    with pytest.raises(PaperInputError):
        a.apply_trusted(a._execution_input(proposal.after.snapshot, fill))
    assert retained(a, k) == before


def test_unreserved_owned_entry_cannot_commit_a_fill():
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha")
    k.process(market())
    k.process(submission())
    k.process(market(3))
    assert_no_fill(a, k, "account_rejected")
    assert a.events == ()


def test_competing_reserved_order_cannot_add_a_second_position_or_duplicate_fees():
    a, one = owned(session="one")
    _, two = owned(account=a, session="two", strategy="beta", sequence=2)
    one.process(market(3))
    fees, position = a.snapshot.fees_paid, a.snapshot.position
    result = two.process(market(3))
    assert two.snapshot.state is OrderState.CANCELLED
    assert result[-1].reason == "account_rejected"
    assert not any(isinstance(e, FillRecord) for e in result)
    assert a.snapshot.position == position and a.snapshot.fees_paid == fees == D("1.2")
    assert a.snapshot.reservations == ()
    assert len([e for e in a.events if e.kind == "settle"]) == 1


def test_gap_settlement_rechecks_all_other_reservations():
    a, one = owned(capital="500", session="one")
    _, two = owned(account=a, session="two", strategy="beta", sequence=2)
    one.process(market(3, bid="200", ask="202"))
    assert one.snapshot.state is OrderState.CANCELLED
    assert a.snapshot.position is None and a.snapshot.fees_paid == 0
    assert len(a.snapshot.reservations) == 1
    assert a.snapshot.reservations[0].order_id == two.snapshot.order_id
    assert a.snapshot.reserved_funds == D("205.2")
    two.process(market(3))
    assert two.snapshot.state is OrderState.FILLED
    assert a.snapshot.reservations == () and a.snapshot.fees_paid == D("1.2")


@pytest.mark.parametrize("capital,bid,ask", [("1000", "100", "102"), ("205.2", "110", "112")])
def test_success_and_denial_replay_are_canonically_identical(capital, bid, ask):
    def replay():
        a, k = owned(capital=capital)
        result = k.process(market(3, bid=bid, ask=ask))
        k.process(market(4, bid="120", ask="122", observed=START + SECOND))
        return a.snapshot.canonical_json(), k.snapshot.canonical_json(), tuple(
            e.canonical_json() for e in a.events), tuple(e.canonical_json() for e in result)
    assert replay() == replay()


def test_successful_standalone_kernel_records_remain_byte_identical():
    a, owned_kernel = owned()
    standalone = PaperOrderKernel(owned_kernel.snapshot.config)
    standalone.process(market())
    standalone.process(submission())
    expected = standalone.process(market(3))
    actual = owned_kernel.process(market(3))
    assert tuple(e.canonical_json() for e in actual) == tuple(e.canonical_json() for e in expected)
    assert owned_kernel.snapshot.canonical_json() == standalone.snapshot.canonical_json()
    assert a.snapshot.fees_paid == D("1.2")


def test_standalone_unaffordable_gap_still_has_original_order_only_semantics():
    a, owned_kernel = owned(capital="205.2")
    standalone = PaperOrderKernel(owned_kernel.snapshot.config)
    standalone.process(market())
    standalone.process(submission())
    result = standalone.process(market(3, bid="110", ask="112"))
    assert standalone.snapshot.state is OrderState.FILLED
    assert len([e for e in result if isinstance(e, FillRecord)]) == 1
    assert a.snapshot.position is None and a.snapshot.reserved_funds == D("205.2")


def test_denial_does_not_reprice_or_reevaluate_execution_risk(monkeypatch):
    a, k = owned(capital="205.2")
    original = k._risk
    calls = []
    def risk(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(k, "_risk", risk)
    k.process(market(3, bid="110", ask="112"))
    assert len(calls) == 1
    assert_no_fill(a, k, "account_unfunded")


@pytest.mark.parametrize("precision", [1, 6, 28])
@pytest.mark.parametrize("capital,bid,ask", [("1000", "100", "102"), ("205.2", "110", "112")])
def test_transactions_do_not_depend_on_decimal_context(precision, capital, bid, ask):
    a, k = owned(capital=capital)
    k.process(market(3, bid=bid, ask=ask))
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        b, other = owned(capital=capital)
        other.process(market(3, bid=bid, ask=ask))
        assert b.snapshot == a.snapshot and b.events == a.events and other.snapshot == k.snapshot


def test_reentrant_mutation_during_financial_preparation_is_denied(monkeypatch):
    import quantlab.paper.accounts as owners
    a, k = owned()
    original = owners.transition_account
    observed = []
    def transition(state, item):
        if isinstance(item, ApplyFill):
            with pytest.raises(PaperInputError):
                k.process(market(4))
            observed.append((a.snapshot, k.snapshot))
        return original(state, item)
    before_account, before_kernel = a.snapshot, k.snapshot
    monkeypatch.setattr(owners, "transition_account", transition)
    k.process(market(3))
    assert observed == [(before_account, before_kernel)]
    assert k.snapshot.state is OrderState.FILLED and a.snapshot.position.quantity == 2


def test_owned_execution_rejects_future_or_backdated_financial_inputs_atomically():
    a, k = owned()
    before = retained(a, k)
    future = market(3).model_copy(update={"quote": market(3).quote.model_copy(
        update={"available_at": START + SECOND})})
    with pytest.raises(PaperInputError):
        k.process(future)
    assert retained(a, k) == before
    assert not any(isinstance(e, FillRecord) for e in k.snapshot.events)


def test_financial_capacity_failure_cannot_publish_a_fill_or_terminal_cancellation(monkeypatch):
    import quantlab.paper.accounts as owners
    a, k = owned()
    before = retained(a, k)
    monkeypatch.setattr(owners, "MAX_ACCOUNT_EVENTS", 1)
    with pytest.raises(PaperInputError):
        k.process(market(3))
    assert retained(a, k) == before
    assert k.snapshot.state is OrderState.ACCEPTED


def test_failure_during_reservation_index_staging_leaves_no_hold(monkeypatch):
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha", costs=COSTS)
    k.process(market())
    k.process(submission())
    before = retained(a, k)
    original = a._stage_indexes
    def fail(financial):
        original(financial)
        raise RuntimeError("after reservation indexes")
    monkeypatch.setattr(a, "_stage_indexes", fail)
    with pytest.raises(RuntimeError):
        a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    assert retained(a, k) == before
    monkeypatch.undo()
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    assert a.snapshot.reserved_funds == D("205.2")


@pytest.mark.parametrize("deny", [False, True])
def test_publication_assignment_failure_rolls_back_order_and_financial_state(monkeypatch, deny):
    a, k = owned(capital="205.2" if deny else "1000")
    before = retained(a, k)
    original = PaperAccount.__setattr__
    armed = [True]
    def fail(owner, name, value):
        original(owner, name, value)
        if owner is a and name == "_publication" and armed[0]:
            armed[0] = False
            # Even at this injected failure, all views describe one consistent root.
            if deny:
                assert_no_fill(a, k, "account_unfunded")
            else:
                assert k.snapshot.state is OrderState.FILLED
                assert a.snapshot.position.quantity == 2 and a.snapshot.fees_paid == D("1.2")
            raise RuntimeError("injected publication assignment failure")
    monkeypatch.setattr(PaperAccount, "__setattr__", fail)
    quote = market(3, bid="110", ask="112") if deny else market(3)
    with pytest.raises(RuntimeError):
        k.process(quote)
    assert retained(a, k) == before
    k.process(quote)
    if deny:
        assert_no_fill(a, k, "account_unfunded")
    else:
        assert k.snapshot.state is OrderState.FILLED and a.snapshot.fees_paid == D("1.2")


def test_failed_kernel_registration_preserves_account_and_session_identity(monkeypatch):
    a = PaperAccount(cfg())
    before = a.snapshot, a.events, a._publication, dict(a._kernels)
    def fail(*args):
        raise RuntimeError("registration publication failed")
    monkeypatch.setattr(a, "_stage_indexes", fail)
    with pytest.raises(RuntimeError):
        a.create_order_kernel(session_id="new", strategy_id="alpha")
    assert (a.snapshot, a.events, a._publication, a._kernels) == before
    monkeypatch.undo()
    k = a.create_order_kernel(session_id="new", strategy_id="alpha")
    assert k.snapshot.state is None


def test_conflicting_cancellation_identity_leaves_released_state_unchanged():
    a, k = owned()
    command = cancel(k)
    k.process(command)
    before = retained(a, k)
    changed = command.model_copy(update={"sequence": 4})
    with pytest.raises(PaperIdentityConflict):
        k.process(changed)
    assert retained(a, k) == before
    assert a.snapshot.reservations == ()


def test_rejected_acceptance_never_creates_reservations_or_financial_events():
    from quantlab.risk import RiskConfig
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha",
        risk=RiskConfig(max_position_quantity=D("1")))
    k.process(market())
    records = k.process(submission())
    assert k.snapshot.state is OrderState.REJECTED
    assert a.snapshot.position is None and a.snapshot.reservations == () and a.events == ()
    assert not any(isinstance(e, FillRecord) for e in records)


def test_equal_timestamp_market_deliveries_keep_transaction_sequence_order():
    a, k = owned()
    before = a.snapshot
    result = k.process(market(3))
    fill = next(e for e in result if isinstance(e, FillRecord))
    assert fill.input_sequence == 3 and fill.timestamp == START
    assert a.events[-1].input_sequence == 2 and a.events[-1].timestamp == START
    assert a.snapshot.last_input_sequence == 2 and a.snapshot.timestamp == START
    assert before.state_version == 1 and before.position is None


def test_owned_orders_cannot_execute_before_the_latest_financial_clock():
    from quantlab.paper import MarkAccount
    from .test_accounts import metadata
    a, one = owned(session="one")
    _, two = owned(account=a, session="two", strategy="beta", sequence=2)
    one.process(market(3))
    a.mark(MarkAccount(**metadata(4, time=START + SECOND),
        source=market(4, observed=START + SECOND)))
    before = retained(a, two)
    with pytest.raises(PaperInputError):
        two.process(market(3))
    assert retained(a, two) == before
    # A causal current quote can commit a non-fill and release only this order's hold.
    two.process(market(3, observed=START + SECOND))
    assert two.snapshot.state is OrderState.CANCELLED
    assert a.snapshot.position.strategy_id == "alpha" and a.snapshot.reservations == ()
