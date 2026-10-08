"""Hand-calculated v1 account fixtures and authority/regression boundaries."""
from datetime import timedelta
from decimal import Decimal, DefaultContext, Inexact, ROUND_DOWN, localcontext

import pytest
from pydantic import ValidationError

from quantlab.backtesting import ExecutionCostConfig, Fill, PositionSide, SignalAction
from quantlab.backtesting.execution import price_execution
from quantlab.data import AssetClass
from quantlab.paper import (
    AccountConfig, AccountEvent, AccountPosition, AccountSnapshot, ApplyFill,
    FundReservation, MarkAccount, OrderSide, PaperAccount, PaperIdentityConflict,
    PaperInputError, ReleaseFunds, ReserveFunds, initialize_account,
    stable_id, transition_account,
)
from .helpers import D, INSTRUMENT, START, SECOND, accepted, cancel, market, submission

COSTS = ExecutionCostConfig(commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1"))


def cfg(**kw):
    return AccountConfig(account_id=kw.pop("account_id", "account"),
        denomination=kw.pop("denomination", "USD"), instrument=kw.pop("instrument", INSTRUMENT),
        starting_capital=kw.pop("starting_capital", D("1000")), timestamp=START, **kw)


def metadata(n, *, event_id=None, strategy="alpha", time=START, account="account", cause=None):
    return dict(event_id=event_id or f"account-{n}", account_id=account, strategy_id=strategy,
        transaction_id=f"tx-{n}", causation_id=cause or f"trusted-{n}", sequence=n, timestamp=time)


def reserve(n=1, *, amount="205.2", rid="reserve-1", order="entry-1", strategy="alpha", **kw):
    oid = stable_id("fixture-order", order)
    cause = stable_id("accepted", order)
    return ReserveFunds(**metadata(n, strategy=strategy, cause=cause, **kw),
        reservation=FundReservation(reservation_id=rid, account_id="account",
            strategy_id=strategy, order_id=oid, accepted_event_id=cause, amount=D(amount)))


def settlement(n=2, *, action=SignalAction.ENTER_LONG, quantity="2", bid="100", ask="102",
        source_seq=None, rid="reserve-1", order=None, strategy="alpha", time=START, costs=COSTS, **kw):
    src = market(n if source_seq is None else source_seq, bid=bid, ask=ask,
        observed=time, processed=time)
    buy = action in (SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT)
    ref = src.quote.ask if buy else src.quote.bid
    price, breakdown = price_execution(ref, D(quantity), costs, buy=buy, spread_adjustment=D("0"))
    fill = Fill(action=action, signal_time=time, execution_time=time,
        reference_price=ref, execution_price=price, quantity=D(quantity),
        slippage_adjustment=costs.slippage, costs=breakdown)
    entry = action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)
    return ApplyFill(**metadata(n, strategy=strategy, time=time, **kw),
        order_id=stable_id("fixture-order", order or ("entry-1" if entry else f"exit-{n}")),
        reservation_id=rid if entry else None, execution=fill, source=src, assumptions=costs)


def opened(*, short=False, costs=COSTS):
    account = PaperAccount(cfg())
    account.apply_trusted(reserve())
    account.apply_trusted(settlement(action=SignalAction.ENTER_SHORT if short else SignalAction.ENTER_LONG,
        costs=costs))
    return account


def mark(n=3, *, bid="110", ask="112", source_seq=None, strategy="alpha", time=START, **kw):
    return MarkAccount(**metadata(n, strategy=strategy, time=time, **kw),
        source=market(n if source_seq is None else source_seq, bid=bid, ask=ask, observed=time, processed=time))


def corrupt(item, **kw):
    return item.model_copy(update=kw)


def assert_atomic(account, command, error=PaperInputError):
    before, events = account.snapshot, account.events
    with pytest.raises(error):
        account.apply_trusted(command)
    assert account.snapshot is before
    assert account.events == events


def test_initial_state_and_content_identity():
    a = PaperAccount(cfg())
    b = initialize_account(cfg())
    assert a.snapshot == b
    assert b.balance == b.equity == b.available_funds == b.running_peak_equity == D("1000")
    assert b.realized_pnl == b.unrealized_pnl == b.fees_paid == b.reserved_funds == b.position_collateral == 0
    assert b.position is None and b.reservations == () and a.events == ()
    assert b.state_version == b.event_sequence == b.last_input_sequence == 0
    assert b.last_event_id == stable_id("paper-account-initial-v1", b.config)
    with pytest.raises(ValidationError):
        b.balance = D("0")


@pytest.mark.parametrize("change", [
    {"starting_capital": D("0")}, {"starting_capital": D("-1")},
    {"starting_capital": D("NaN")}, {"starting_capital": 1000.0},
    {"starting_capital": "1000"}, {"account_id": ""}, {"account_id": "two words"},
    {"denomination": "EUR"}, {"denomination": "usd"}, {"policy": "cash"},
    {"timestamp": START.replace(tzinfo=None)}, {"extra": True},
    {"starting_capital": D("1e5000")},
])
def test_config_rejects_unsupported_and_invalid(change):
    values = cfg().model_dump(mode="python")
    values.update(change)
    with pytest.raises((ValueError, TypeError)):
        AccountConfig(**values)


@pytest.mark.parametrize("instrument", [
    INSTRUMENT.model_copy(update={"contract_multiplier": D("2")}),
    INSTRUMENT.model_copy(update={"asset_class": AssetClass.CRYPTO}),
    INSTRUMENT.model_copy(update={"base_currency": "EUR"}),
])
def test_unsupported_instruments(instrument):
    with pytest.raises(ValidationError):
        cfg(instrument=instrument)


@pytest.mark.parametrize("field,value", [
    ("balance", D("999")), ("equity", D("999")), ("available_funds", D("999")),
    ("reserved_funds", D("1")), ("position_collateral", D("1")),
    ("realized_pnl", D("1")), ("unrealized_pnl", D("1")), ("fees_paid", D("1")),
    ("state_version", 1), ("event_sequence", 1), ("last_input_sequence", 1),
    ("running_peak_equity", D("999")),
])
def test_snapshot_reconciliation(field, value):
    values = initialize_account(cfg()).model_dump(mode="python")
    values[field] = value
    with pytest.raises(ValidationError):
        AccountSnapshot(**values)


def test_reservation_release_and_retries():
    a = PaperAccount(cfg())
    r = reserve()
    e = a.apply_trusted(r)
    assert a.snapshot.available_funds == D("794.8")
    assert a.snapshot.reserved_funds == D("205.2")
    assert a.apply_trusted(r) is e
    release = ReleaseFunds(**metadata(2), reservation_id="reserve-1",
        order_id=r.reservation.order_id, reason="cancelled")
    e2 = a.apply_trusted(release)
    assert a.snapshot.available_funds == D("1000") and a.snapshot.reserved_funds == 0
    assert a.apply_trusted(release) is e2
    assert a.snapshot.state_version == 2
    assert_atomic(a, reserve(3), PaperIdentityConflict)


def test_aggregate_reservations_prevent_overspend():
    a = PaperAccount(cfg())
    a.apply_trusted(reserve(amount="600"))
    a.apply_trusted(reserve(2, amount="400", rid="reserve-2", order="entry-2", strategy="beta"))
    assert a.snapshot.available_funds == 0
    assert_atomic(a, reserve(3, amount="0.01", rid="reserve-3", order="entry-3"))


@pytest.mark.parametrize("change", [
    {"amount": D("0")}, {"amount": D("-1")}, {"amount": D("NaN")},
    {"amount": 1.0}, {"account_id": "foreign"}, {"strategy_id": "foreign"},
    {"accepted_event_id": "a" * 64},
])
def test_invalid_reservation_is_atomic(change):
    a = PaperAccount(cfg())
    r = reserve()
    assert_atomic(a, corrupt(r, reservation=corrupt(r.reservation, **change)))


def test_duplicate_order_and_conflicting_reservation_ids():
    a = PaperAccount(cfg())
    a.apply_trusted(reserve())
    assert_atomic(a, reserve(2, rid="new"), PaperIdentityConflict)
    assert_atomic(a, reserve(2, order="new"), PaperIdentityConflict)
    assert_atomic(a, reserve(amount="200"), PaperIdentityConflict)


@pytest.mark.parametrize("change", [
    {"reservation_id": "missing"}, {"strategy_id": "beta"}, {"order_id": "b" * 64},
    {"account_id": "other"}, {"sequence": 1}, {"timestamp": START - SECOND},
])
def test_release_ownership_and_chronology(change):
    a = PaperAccount(cfg())
    r = reserve()
    a.apply_trusted(r)
    command = ReleaseFunds(**metadata(2), reservation_id="reserve-1",
        order_id=r.reservation.order_id, reason="rejected")
    assert_atomic(a, corrupt(command, **change))


@pytest.mark.parametrize("short,basis,collateral,available", [
    (False, "102", "204", "794.8"), (True, "100", "200", "798.8"),
])
def test_open_hand_calculated(short, basis, collateral, available):
    a = opened(short=short)
    s, p = a.snapshot, a.snapshot.position
    assert p.direction is (PositionSide.SHORT if short else PositionSide.LONG)
    assert p.entry_basis == D(basis) and p.quantity == 2 and p.cost_basis == D(collateral)
    assert p.account_id == "account" and p.strategy_id == "alpha" and p.instrument == INSTRUMENT
    assert p.valuation.quote.source_id == "synthetic"
    assert s.balance == D("998.8") and s.fees_paid == p.fees_paid == D("1.2")
    assert s.realized_pnl == 0 and s.net_realized_pnl == D("-1.2")
    assert s.unrealized_pnl == p.unrealized_pnl == D("-4")
    assert s.equity == D("994.8") and s.available_funds == D(available)
    assert s.reserved_funds == 0 and s.reservations == ()


@pytest.mark.parametrize("short,bid,ask,pnl", [
    (False, "110", "112", "16"), (False, "90", "92", "-24"),
    (True, "88", "90", "20"), (True, "108", "110", "-20"),
])
def test_liquidation_marks(short, bid, ask, pnl):
    a = opened(short=short)
    a.mark(mark(bid=bid, ask=ask))
    assert a.snapshot.unrealized_pnl == D(pnl)
    assert a.snapshot.equity == D("998.8") + D(pnl)
    assert a.snapshot.balance == D("998.8")
    assert a.snapshot.position.valuation.quote.bid == D(bid)
    assert a.snapshot.available_funds == (D("798.8") if short else D("794.8"))


@pytest.mark.parametrize("short,action,bid,ask,gross,unrealized", [
    (False, SignalAction.EXIT_LONG, "110", "112", "8", "8"),
    (True, SignalAction.EXIT_SHORT, "88", "90", "10", "10"),
    (False, SignalAction.EXIT_LONG, "90", "92", "-12", "-12"),
    (True, SignalAction.EXIT_SHORT, "108", "110", "-10", "-10"),
])
def test_partial_reduce_and_close_hand_calculated(short, action, bid, ask, gross, unrealized):
    a = opened(short=short)
    partial = settlement(3, action=action, quantity="1", bid=bid, ask=ask)
    event = a.apply_trusted(partial)
    s = a.snapshot
    assert s.position.quantity == 1
    assert s.position.cost_basis == (D("100") if short else D("102"))
    assert s.realized_pnl == s.position.realized_pnl == D(gross)
    assert s.unrealized_pnl == D(unrealized)
    assert s.fees_paid == s.position.fees_paid == D("2.3")
    assert s.balance == D("1000") + D(gross) - D("2.3")
    assert s.available_funds == s.balance - s.position_collateral
    assert a.apply_trusted(partial) is event and a.snapshot is s
    close = settlement(4, action=action, quantity="1", bid=bid, ask=ask)
    a.apply_trusted(close)
    s = a.snapshot
    assert s.position.quantity == s.position.cost_basis == s.position_collateral == s.unrealized_pnl == 0
    assert s.realized_pnl == D(gross) * 2 and s.fees_paid == D("3.4")
    assert s.balance == s.equity == s.available_funds == D("1000") + D(gross) * 2 - D("3.4")


def test_slippage_is_price_effect_not_second_debit():
    costs = ExecutionCostConfig(slippage=D("0.5"),
        commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1"))
    a = opened(costs=costs)
    assert a.snapshot.position.entry_basis == D("102.5")
    a.apply_trusted(settlement(3, action=SignalAction.EXIT_LONG, bid="110", ask="112", costs=costs))
    assert a.snapshot.realized_pnl == D("14")  # (109.5 - 102.5) * 2
    assert a.snapshot.fees_paid == D("2.4")
    assert a.snapshot.balance == D("1011.6")


@pytest.mark.parametrize("change", [
    {"strategy_id": "beta"}, {"account_id": "other"}, {"reservation_id": None},
    {"order_id": "b" * 64}, {"sequence": 1}, {"timestamp": START - SECOND},
])
def test_open_invalid_input_is_atomic(change):
    a = PaperAccount(cfg())
    a.apply_trusted(reserve())
    assert_atomic(a, corrupt(settlement(), **change))


@pytest.mark.parametrize("action,quantity,strategy", [
    (SignalAction.ENTER_LONG, "2", "alpha"), (SignalAction.ENTER_SHORT, "2", "alpha"),
    (SignalAction.EXIT_SHORT, "1", "alpha"), (SignalAction.EXIT_LONG, "3", "alpha"),
    (SignalAction.EXIT_LONG, "1", "beta"),
])
def test_reject_scale_in_reversal_over_reduction_and_conflicting_owner(action, quantity, strategy):
    a = opened()
    assert_atomic(a, settlement(3, action=action, quantity=quantity, strategy=strategy, order="new"))


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity", "0.5"])
def test_bad_quantity(quantity):
    a = opened()
    item = settlement(3, action=SignalAction.EXIT_LONG, quantity="1")
    assert_atomic(a, corrupt(item, execution=corrupt(item.execution, quantity=D(quantity))))


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity", "111"])
def test_bad_fill_price(price):
    a = opened()
    item = settlement(3, action=SignalAction.EXIT_LONG, quantity="1")
    assert_atomic(a, corrupt(item, execution=corrupt(item.execution, execution_price=D(price))))


def test_flat_reduction_and_mark_rejected():
    a = PaperAccount(cfg())
    assert_atomic(a, settlement(action=SignalAction.EXIT_LONG))
    assert_atomic(a, mark())


def test_insufficient_entry_capacity_and_price_gap():
    a = PaperAccount(cfg(starting_capital=D("205.2")))
    a.apply_trusted(reserve())
    # Reservation is an estimate, not price protection.
    assert_atomic(a, settlement(bid="110", ask="112"))
    a.apply_trusted(settlement())
    assert a.snapshot.available_funds == 0


def test_fill_without_reservation_rejected():
    a = PaperAccount(cfg())
    assert_atomic(a, settlement())


@pytest.mark.parametrize("change", [
    {"sequence": 2}, {"timestamp": START - SECOND}, {"account_id": "other"},
    {"strategy_id": "beta"},
])
def test_bad_mark_metadata(change):
    a = opened()
    assert_atomic(a, corrupt(mark(), **change))


def test_future_unavailable_stale_and_wrong_instrument_marks():
    a = opened()
    cmd = mark()
    for src in [
        market(3, observed=START + SECOND),
        market(2),
        market(3, observed=START - SECOND, delivered=START),
        corrupt(cmd.source, quote=corrupt(cmd.source.quote, instrument_id="other")),
        corrupt(cmd.source, quote=corrupt(cmd.source.quote, available_at=START + SECOND)),
        corrupt(cmd.source, quote=corrupt(cmd.source.quote, bid=D("0"))),
    ]:
        assert_atomic(a, corrupt(cmd, source=src))


def test_equal_time_distinct_observations_and_immutable_prefix():
    a = opened()
    before, prefix = a.snapshot, a.events
    first = a.mark(mark(bid="110", ask="112"))
    a.mark(mark(4, bid="120", ask="122"))
    assert before.equity == D("994.8")
    assert prefix[-1].equity == D("994.8") and first.equity == D("1014.8")
    assert a.snapshot.equity == D("1034.8")
    assert a.mark(mark(bid="110", ask="112")) is first
    assert a.snapshot.equity == D("1034.8")


def test_fee_and_quantity_idempotency_and_rebound_execution():
    a = PaperAccount(cfg())
    a.apply_trusted(reserve())
    cmd = settlement()
    event = a.apply_trusted(cmd)
    for _ in range(5):
        assert a.apply_trusted(cmd) is event
    assert a.snapshot.position.quantity == 2 and a.snapshot.fees_paid == D("1.2")
    assert_atomic(a, corrupt(cmd, event_id="new", sequence=3), PaperIdentityConflict)
    assert_atomic(a, corrupt(cmd, strategy_id="beta"), PaperIdentityConflict)
    assert_atomic(a, corrupt(cmd, execution=corrupt(cmd.execution, quantity=D("-1"))), PaperIdentityConflict)


def test_event_hash_chain_and_canonical_decimal_normalization():
    a = opened()
    previous = initialize_account(cfg()).last_event_id
    for n, e in enumerate(a.events, 1):
        assert e.previous_event_id == previous and e.sequence == e.after_version == n
        assert e.before_version == n - 1
        assert e.account_id == "account" and e.strategy_id == "alpha"
        assert e.event_id == stable_id("paper-account-event-v1", e.model_dump(exclude={"event_id"}))
        assert AccountEvent.model_validate_json(e.canonical_json()) == e
        previous = e.event_id
    with pytest.raises(ValidationError):
        AccountEvent.model_validate(corrupt(a.events[-1], equity=D("1")))
    assert cfg(starting_capital=D("1000.00")).canonical_json() == cfg().canonical_json()


def test_pure_transition_does_not_mutate_input_and_matches_owner():
    initial = initialize_account(cfg())
    updated, event = transition_account(initial, reserve())
    a = PaperAccount(cfg())
    assert a.apply_trusted(reserve()) == event and a.snapshot == updated
    assert initial.available_funds == 1000 and initial.state_version == 0


def test_independent_accounts_no_shared_state_or_authority():
    a, b = opened(), PaperAccount(cfg(account_id="second"))
    assert b.snapshot.balance == 1000 and b.events == ()
    assert_atomic(b, mark())
    assert a.snapshot.position.quantity == 2


@pytest.mark.parametrize("precision", [1, 6, 28, 100])
def test_decimal_context_independent(precision):
    expected = opened()
    expected.apply_trusted(settlement(3, action=SignalAction.EXIT_LONG, quantity="1", bid="110", ask="112"))
    with localcontext() as ctx:
        ctx.prec = precision
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        ctx.Emax = 9
        ctx.Emin = -9
        actual = opened()
        actual.apply_trusted(settlement(3, action=SignalAction.EXIT_LONG, quantity="1", bid="110", ask="112"))
        assert actual.snapshot == expected.snapshot and actual.events == expected.events
        assert ctx.prec == precision and ctx.rounding == ROUND_DOWN


def test_exact_accounts_preserve_more_than_34_digits_without_changing_backtests():
    capital = D("1000.123456789012345678901234567890123456789")
    a = PaperAccount(cfg(starting_capital=capital))
    a.apply_trusted(reserve())
    a.apply_trusted(settlement())
    assert a.snapshot.balance == D("998.923456789012345678901234567890123456789")


def test_account_owned_kernel_adapter_full_flow_and_retry():
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha", costs=COSTS)
    assert k.snapshot.config.flat_equity == a.snapshot.equity
    k.process(market())
    k.process(submission())
    r = a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    assert a.snapshot.reserved_funds == D("205.2")
    k.process(market(3))
    e = a.settle_order(k, event_id="settle", sequence=2, timestamp=START)
    assert a.snapshot.position.quantity == 2 and a.snapshot.balance == D("998.8")
    assert a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START) is r
    assert a.settle_order(k, event_id="settle", sequence=2, timestamp=START) is e
    with pytest.raises(PaperIdentityConflict):
        a.settle_order(k, event_id="settle", sequence=3, timestamp=START)
    with pytest.raises(PaperInputError):
        a.create_order_kernel(session_id="new", strategy_id="beta")


def test_adapter_cancellation_releases_owned_reservation():
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha")
    k.process(market())
    k.process(submission())
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    k.process(cancel(k))
    event = a.release_order(k, event_id="release", sequence=2, timestamp=START)
    assert a.snapshot.available_funds == 1000 and a.snapshot.reserved_funds == 0
    assert a.release_order(k, event_id="release", sequence=2, timestamp=START) is event


def test_adapter_rejects_foreign_handles_and_unaccepted_or_unfilled_orders():
    a = PaperAccount(cfg())
    foreign = accepted()
    for method in (a.reserve_order, a.release_order, a.settle_order):
        with pytest.raises(PaperInputError):
            method(foreign, event_id="foreign", sequence=1, timestamp=START)
    k = a.create_order_kernel(session_id="session", strategy_id="alpha")
    for method in (a.reserve_order, a.release_order, a.settle_order):
        with pytest.raises(PaperInputError):
            method(k, event_id="bad", sequence=1, timestamp=START)
    assert a.snapshot.state_version == 0
    with pytest.raises(PaperInputError):
        a.create_order_kernel(session_id="session", strategy_id="alpha")


def test_reservation_and_settlement_account_clocks_are_independent_from_kernel_sequences():
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha")
    k.process(market())
    k.process(submission())
    a.reserve_order(k, event_id="reserve", sequence=10, timestamp=START)
    k.process(market(3, observed=START + SECOND))
    a.settle_order(k, event_id="settle", sequence=20, timestamp=START + SECOND)
    # Automatic settlement owns the next account sequence; terminal adapters only acknowledge.
    assert a.snapshot.last_input_sequence == 11 and a.snapshot.position.valuation.sequence == 3
    assert a.events[-1].input_sequence == 11


def test_delayed_execution_preserves_original_observation_and_effective_time():
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha")
    k.process(market())
    k.process(submission())
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    k.process(market(3, observed=START, delivered=START + SECOND, processed=START + SECOND))
    a.settle_order(k, event_id="settle", sequence=2, timestamp=START + SECOND)
    assert a.snapshot.position.entry_time == START + SECOND
    assert a.snapshot.position.valuation.quote.timestamp == START


def test_structurally_malformed_contract_cannot_mutate_state():
    a = opened()
    assert_atomic(a, MarkAccount.model_construct(event_id="bad"))
    assert_atomic(a, {"kind": "mark"})


def test_retention_caps_fail_atomically(monkeypatch):
    import quantlab.paper.accounts as owners
    import quantlab.paper.accounting as transitions
    a = PaperAccount(cfg())
    monkeypatch.setattr(transitions, "MAX_RESERVATIONS", 1)
    a.apply_trusted(reserve())
    assert_atomic(a, reserve(2, rid="two", order="two"))
    monkeypatch.setattr(owners, "MAX_ACCOUNT_EVENTS", 1)
    assert_atomic(a, mark())
    monkeypatch.setattr(owners, "MAX_KERNELS", 1)
    a.create_order_kernel(session_id="one", strategy_id="alpha")
    with pytest.raises(PaperInputError):
        a.create_order_kernel(session_id="two", strategy_id="beta")


def test_journal_is_not_copied_or_serialized_on_update():
    a = opened()
    journal = a._journal
    a.mark(mark())
    assert a._journal is journal
    assert a.events is not a.events and len(a.events) == 3


def test_forex_contract_is_explicitly_unsupported():
    values = INSTRUMENT.model_dump(mode="python")
    values.update(asset_class=AssetClass.FOREX, base_currency="EUR",
        pip_size=D("0.01"), lot_size=D("100000"))
    instrument = type(INSTRUMENT)(**values)
    with pytest.raises(ValidationError):
        cfg(instrument=instrument)


def test_default_decimal_context_cannot_change_account_results():
    original = DefaultContext.copy()
    try:
        DefaultContext.prec = 1
        DefaultContext.rounding = ROUND_DOWN
        DefaultContext.traps[Inexact] = True
        a = opened()
        a.apply_trusted(settlement(3, action=SignalAction.EXIT_LONG, bid="110", ask="112"))
        assert a.snapshot.balance == D("1013.6")
    finally:
        DefaultContext.prec = original.prec
        DefaultContext.rounding = original.rounding
        DefaultContext.Emin = original.Emin
        DefaultContext.Emax = original.Emax
        DefaultContext.traps = original.traps.copy()


def test_full_close_then_reopen_keeps_account_totals():
    a = opened()
    a.apply_trusted(settlement(3, action=SignalAction.EXIT_LONG, bid="110", ask="112"))
    a.apply_trusted(reserve(4, order="second", rid="second", strategy="beta"))
    a.apply_trusted(settlement(5, action=SignalAction.ENTER_SHORT,
        order="second", rid="second", strategy="beta"))
    assert a.snapshot.realized_pnl == D("16")
    assert a.snapshot.fees_paid == D("3.6")
    assert a.snapshot.position.strategy_id == "beta"
    assert a.snapshot.position.realized_pnl == 0
    assert a.snapshot.balance == D("1012.4")


def test_settled_execution_cannot_be_rebound_to_new_financial_identity_or_order():
    a = opened()
    a.apply_trusted(settlement(3, action=SignalAction.EXIT_LONG))
    a.apply_trusted(reserve(4, order="second", rid="second"))
    old = settlement(5, order="second", rid="second", cause="trusted-2")
    assert_atomic(a, old, PaperIdentityConflict)


def test_get_input_record_preserves_quote_pricing_and_transaction():
    a = opened()
    assert a.get_input_record("account-2") == settlement().canonical_json()
    with pytest.raises(PaperInputError):
        a.get_input_record("unknown")


def test_price_effect_rounding_from_18a_is_rejected_by_exact_account_policy():
    a = PaperAccount(cfg())
    a.apply_trusted(reserve(amount="300"))
    costs = ExecutionCostConfig(slippage=D("1"))
    # 18A allows precision-34 reconciliation. Exact v1 accounts refuse this fill.
    item = settlement(bid="100", ask="102.000000000000000000000000000000000001", costs=costs)
    assert item.execution.execution_price == D("103")
    assert_atomic(a, item)


def test_rounded_commission_from_18a_is_rejected_by_exact_account_policy():
    a = PaperAccount(cfg())
    a.apply_trusted(reserve(amount="300"))
    costs = ExecutionCostConfig(commission_per_unit=D("0.123456789012345678901234567890123456"))
    assert_atomic(a, settlement(costs=costs))


def test_extreme_exact_output_failure_preserves_all_state_and_retry_capacity():
    capital = D("9" * 4096)
    a = PaperAccount(cfg(starting_capital=capital))
    a.apply_trusted(reserve(amount="205"))
    before = a.snapshot
    # Subtracting fractional fees from 4096 integer digits exceeds exact capacity.
    assert_atomic(a, settlement())
    assert a.snapshot is before
    assert "account-2" not in a._seen


@pytest.mark.parametrize("changes", [
    {"entry_basis": D("0")}, {"quantity": D("-1")}, {"cost_basis": D("1")},
    {"unrealized_pnl": D("1")}, {"direction": "long"},
    {"valuation": None}, {"fees_paid": D("-1")},
])
def test_position_model_validation(changes):
    p = opened().snapshot.position
    with pytest.raises((ValidationError, TypeError)):
        AccountPosition.model_validate(corrupt(p, **changes))


def test_inexact_or_over_budget_short_loss_is_rejected_without_liquidation_policy():
    a = opened(short=True)
    assert_atomic(a, settlement(3, action=SignalAction.EXIT_SHORT, bid="1000", ask="1002"))
    assert a.snapshot.position.quantity == 2  # no fabricated liquidation/credit
    a.mark(mark(bid="1000", ask="1002"))
    assert a.snapshot.equity == D("-805.2")
    assert a.snapshot.available_funds == D("798.8")


def test_stale_quote_timestamp_cannot_replace_newer_valuation():
    a = opened()
    a.mark(mark(3, time=START + SECOND))
    old = market(4, observed=START, delivered=START + 2 * SECOND, processed=START + 2 * SECOND)
    assert_atomic(a, corrupt(mark(4, time=START + 2 * SECOND), source=old))


def test_quote_availability_cannot_regress():
    a = opened()
    source = market(3, observed=START, available=START + SECOND,
        delivered=START + SECOND, processed=START + SECOND)
    a.mark(corrupt(mark(3, time=START + SECOND), source=source))
    source = market(4, observed=START, available=START,
        delivered=START + 2 * SECOND, processed=START + 2 * SECOND)
    assert_atomic(a, corrupt(mark(4, time=START + 2 * SECOND), source=source))


@pytest.mark.parametrize("change", [
    {"sequence": 0}, {"sequence": True}, {"sequence": "1"},
    {"timestamp": START.replace(tzinfo=None)}, {"event_id": []}, {"event_id": ""},
])
def test_adapter_strict_validation(change):
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha")
    k.process(market())
    k.process(submission())
    request = dict(event_id="reserve", sequence=1, timestamp=START)
    request.update(change)
    before = a.snapshot
    with pytest.raises(PaperInputError):
        a.reserve_order(k, **request)
    assert a.snapshot is before


@pytest.mark.parametrize("session,strategy", [([], "alpha"), ("", "alpha"), ("one", ""), ("one", 1)])
def test_kernel_attribution_is_strict(session, strategy):
    a = PaperAccount(cfg())
    with pytest.raises(PaperInputError):
        a.create_order_kernel(session_id=session, strategy_id=strategy)
    assert not a._kernels


def test_adapter_pre_fill_risk_cancellation_releases_reservation():
    from quantlab.risk import RiskConfig
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha",
        risk=RiskConfig(max_notional_exposure=D("210")))
    k.process(market())
    k.process(submission())
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    k.process(market(3, bid="110", ask="112"))
    a.release_order(k, event_id="release", sequence=2, timestamp=START)
    assert a.snapshot.available_funds == 1000
    assert a.snapshot.position is None


def test_adapter_aggregate_orders_share_funds_but_not_aggregate_risk():
    a = PaperAccount(cfg(starting_capital=D("300")))
    one = a.create_order_kernel(session_id="one", strategy_id="alpha")
    two = a.create_order_kernel(session_id="two", strategy_id="beta")
    for k in (one, two):
        k.process(market())
        k.process(submission())
    a.reserve_order(one, event_id="reserve-one", sequence=1, timestamp=START)
    with pytest.raises(PaperInputError):
        a.reserve_order(two, event_id="reserve-two", sequence=2, timestamp=START)
    assert a.snapshot.reserved_funds == 204
    assert a.snapshot.available_funds == 96


def test_adapter_gap_failure_cancels_without_a_fill_and_releases_funds():
    a = PaperAccount(cfg(starting_capital=D("205.2")))
    k = a.create_order_kernel(session_id="session", strategy_id="alpha", costs=COSTS)
    k.process(market())
    k.process(submission())
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    k.process(market(3, bid="110", ask="112"))
    before = a.snapshot
    with pytest.raises(PaperInputError):
        a.settle_order(k, event_id="settle", sequence=2, timestamp=START)
    assert a.snapshot is before and a.snapshot.reserved_funds == 0
    assert a.snapshot.available_funds == D("205.2")
    assert a.snapshot.position is None and a.snapshot.fees_paid == 0
    from quantlab.paper import FillRecord, OrderState
    assert k.snapshot.state is OrderState.CANCELLED
    assert k.snapshot.events[-1].reason == "account_unfunded"
    assert not any(isinstance(e, FillRecord) for e in k.snapshot.events)


def test_no_files_network_workers_or_clocks_in_financial_transitions(monkeypatch):
    import builtins
    import socket
    import subprocess
    import threading
    import time
    from pathlib import Path
    a = PaperAccount(cfg())
    commands = [reserve(), settlement(), mark(),
        settlement(4, action=SignalAction.EXIT_LONG, bid="110", ask="112")]
    def denied(*args, **kwargs):
        raise AssertionError("I/O or wall clock forbidden")
    with monkeypatch.context() as p:
        for owner, name in [(builtins, "open"), (Path, "open"), (socket, "socket"),
                (socket, "create_connection"), (subprocess, "Popen"),
                (threading.Thread, "start"), (time, "time"), (time, "monotonic")]:
            p.setattr(owner, name, denied)
        for command in commands:
            a.apply_trusted(command)
    assert a.snapshot.balance == D("1013.6")


def test_update_cost_does_not_serialize_or_copy_full_history(monkeypatch):
    a = opened()
    original = AccountEvent.canonical_json
    old = a.events[-1].sequence
    def bounded(event):
        assert event.sequence > old
        return original(event)
    monkeypatch.setattr(AccountEvent, "canonical_json", bounded)
    journal, seen = a._journal, a._seen
    sizes = []
    for n in range(3, 131):
        a.mark(mark(n))
        sizes.append(len(a.snapshot.canonical_json()))
    assert a._journal is journal and a._seen is seen
    assert max(sizes) - min(sizes) < 32
    assert len(a.events) == 130


def test_adapter_reservation_uses_exact_fee_estimate_before_18a_rounding():
    a = PaperAccount(cfg())
    commission = D("0.123456789012345678901234567890123456")
    k = a.create_order_kernel(session_id="session", strategy_id="alpha",
        costs=ExecutionCostConfig(commission_per_unit=commission))
    k.process(market())
    k.process(submission())
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    assert a.snapshot.reserved_funds == D("204.246913578024691357802469135780246912")
    k.process(market(3))
    before = a.snapshot
    with pytest.raises(PaperInputError):
        a.settle_order(k, event_id="settle", sequence=2, timestamp=START)
    assert a.snapshot is before
    from quantlab.paper import FillRecord, OrderState
    assert k.snapshot.state is OrderState.CANCELLED
    assert k.snapshot.events[-1].reason == "account_rejected"
    assert a.snapshot.reserved_funds == 0 and a.snapshot.fees_paid == 0
    assert not any(isinstance(e, FillRecord) for e in k.snapshot.events)


def test_adapter_invalid_reservation_price_is_atomic():
    a = PaperAccount(cfg())
    k = a.create_order_kernel(session_id="session", strategy_id="alpha",
        costs=ExecutionCostConfig(slippage=D("101")))
    k.process(market())
    k.process(submission(side=OrderSide.SELL))
    before = a.snapshot
    with pytest.raises(PaperInputError):
        a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    assert a.snapshot is before and a.events == ()
