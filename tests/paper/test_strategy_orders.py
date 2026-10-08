"""Narrow next-open adapter, owner risk, prefunding and transactional rollback."""
from decimal import Context, Inexact, Rounded, ROUND_DOWN, localcontext

import pytest
from pydantic import ValidationError
from quantlab.backtesting import PositionSide, SignalAction
from quantlab.data import Timeframe
from quantlab.paper import (
    FillRecord, OrderSide, OrderState, PaperIdentityConflict, PaperInputError,
    StrategyOrderAdapter,
)
from quantlab.risk import RiskConfig
from quantlab.strategies import Direction
from tests.backtesting.helpers import D, MINUTE, START, strategy
from .strategy_helpers import close_quote, delivery, opening, ready, runtime
from .test_strategy_admission import reseal


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_valid_entry_conversion_and_owned_execution(side):
    spec = strategy(no_exit=True, direction=Direction.LONG if side is OrderSide.BUY else Direction.SHORT)
    rt, account, args, ev = runtime(spec=spec)
    d = delivery(close=101 if side is OrderSide.BUY else 99)
    decision = rt.process(d)
    adapter = StrategyOrderAdapter(rt, account)
    accepted = adapter.submit(close_quote(d))
    assert adapter.snapshot.state is OrderState.ACCEPTED
    assert adapter.snapshot.submission.side is side
    assert adapter.snapshot.submission.quantity == rt.config.quantity
    assert adapter.snapshot.submission.timestamp == d.bar.end_time
    assert account.snapshot.position is None
    assert account.snapshot.reserved_funds > 0
    assert account.snapshot.reservations[0].strategy_id == rt.config.strategy_id
    records = adapter.process_open(opening(d))
    fill = next(r for r in records if isinstance(r, FillRecord))
    assert fill.execution.reference_price == D("101")
    assert fill.execution.signal_time == fill.execution.execution_time == d.bar.end_time
    assert fill.source.sequence > fill.submission.sequence
    assert fill.execution.action is (SignalAction.ENTER_LONG if side is OrderSide.BUY else SignalAction.ENTER_SHORT)
    assert account.snapshot.position.direction is (PositionSide.LONG if side is OrderSide.BUY else PositionSide.SHORT)
    assert account.snapshot.position.strategy_id == rt.config.strategy_id
    assert adapter.snapshot.state is OrderState.FILLED
    assert account.snapshot.reserved_funds == 0
    assert account.snapshot.position_collateral == D("202")
    assert len(account.events) == 2 and account.events[-1].kind == "settle"
    assert account.events[-1].causation_id == fill.event_id
    assert adapter.openings == (opening(d),)


def test_close_quote_and_submission_are_not_allowed_to_fill():
    rt, adapter, account, d, _, _ = ready()
    records = adapter.submit(close_quote(d))
    assert not any(isinstance(r, FillRecord) for r in records)
    assert account.snapshot.position is None
    # Ordinary quotes cannot be reinterpreted as the next bar's opening price.
    with pytest.raises(PaperInputError): adapter.process_open(close_quote(d, sequence=3))
    assert account.snapshot.position is None


def test_exact_entry_and_opening_retries_cannot_reserve_or_fill_twice():
    rt, adapter, account, d, _, _ = ready()
    close, op = close_quote(d), opening(d)
    accepted = adapter.submit(close)
    reservation_snapshot = account.snapshot
    assert adapter.submit(close) == accepted
    assert account.snapshot == reservation_snapshot
    filled = adapter.process_open(op)
    account_snapshot, order_snapshot = account.snapshot, adapter.snapshot
    assert adapter.process_open(op) == filled
    assert adapter.submit(close) == accepted
    assert account.snapshot == account_snapshot and adapter.snapshot == order_snapshot
    assert len([r for r in adapter.snapshot.events if isinstance(r, FillRecord)]) == 1


@pytest.mark.parametrize("kind", ["close_identity", "opening_identity", "opening_interval", "opening_sequence"])
def test_conflicting_input_identity_fails_explicitly(kind):
    rt, adapter, account, d, _, _ = ready()
    close, op = close_quote(d), opening(d)
    adapter.submit(close)
    adapter.process_open(op)
    before = account.snapshot, adapter.snapshot, adapter.openings
    if kind == "close_identity": operation = lambda: adapter.submit(close_quote(d, ask="103"))
    elif kind == "opening_identity": operation = lambda: adapter.process_open(opening(d, price="102"))
    elif kind == "opening_interval": operation = lambda: adapter.process_open(op.model_copy(update={"bar_end": op.bar_end+MINUTE}))
    else: operation = lambda: adapter.process_open(op.model_copy(update={"sequence": 4}))
    with pytest.raises(PaperIdentityConflict): operation()
    assert (account.snapshot, adapter.snapshot, adapter.openings) == before


@pytest.mark.parametrize("kind", ["future_bar", "wrong_close", "wrong_timeframe", "wrong_instrument", "early_sequence",
                                  "delayed", "spread_quote", "retrospective_ohlc"])
def test_next_open_constraints_leave_account_and_order_unchanged(kind):
    rt, adapter, account, d, _, _ = ready()
    adapter.submit(close_quote(d))
    op = opening(d)
    before = account.snapshot, adapter.snapshot
    if kind == "future_bar":
        later = op.bar_start+MINUTE
        op = op.model_copy(update={"bar_start": later, "bar_end": later+MINUTE,
            "timestamp": later, "delivered_at": later,
            "quote": op.quote.model_copy(update={"timestamp": later, "available_at": later})})
    elif kind == "wrong_close": op = op.model_copy(update={"previous_close_id": "unknown"})
    elif kind == "wrong_timeframe": op = op.model_copy(update={"timeframe": Timeframe.H1})
    elif kind == "wrong_instrument": op = op.model_copy(update={"quote": op.quote.model_copy(update={"instrument_id": "other"})})
    elif kind == "early_sequence": op = op.model_copy(update={"sequence": 2})
    elif kind == "delayed": op = op.model_copy(update={"delivered_at": op.delivered_at+MINUTE})
    elif kind == "spread_quote": op = op.model_copy(update={"quote": op.quote.model_copy(update={"ask": D("102")})})
    else: op = delivery(1)
    with pytest.raises(PaperInputError): adapter.process_open(op)
    assert (account.snapshot, adapter.snapshot) == before


def test_completed_next_bar_prevents_retroactive_opening_fill_but_retries_remain_safe():
    rt, adapter, account, d, _, _ = ready()
    adapter.submit(close_quote(d))
    rt.process(delivery(1))
    before = account.snapshot, adapter.snapshot
    with pytest.raises(PaperInputError, match="missed next opening"): adapter.process_open(opening(d))
    assert (account.snapshot, adapter.snapshot) == before
    rt2, adapter2, account2, d2, _, _ = ready()
    adapter2.submit(close_quote(d2))
    filled = adapter2.process_open(opening(d2))
    rt2.process(delivery(1))
    assert adapter2.process_open(opening(d2)) == filled


@pytest.mark.parametrize("kind", ["no_intent", "fake_runtime", "fake_account", "foreign_account", "competing_adapter",
                                  "agent_decision", "risk_override", "quantity_override", "exit", "reversal"])
def test_no_authority_expansion_or_unapproved_order_path(kind):
    rt, account, _, _ = runtime()
    before = account.snapshot
    if kind == "no_intent":
        adapter = StrategyOrderAdapter(rt, account)
        operation = lambda: adapter.submit(close_quote(delivery()))
    elif kind == "fake_runtime": operation = lambda: StrategyOrderAdapter(rt.admission, account)
    elif kind == "fake_account": operation = lambda: StrategyOrderAdapter(rt, account.snapshot)
    elif kind == "foreign_account":
        other, another, _, _ = runtime()
        another._publication.snapshot.config  # Actual incompatible owner reference.
        from quantlab.paper import AccountConfig, PaperAccount
        foreign = PaperAccount(AccountConfig(**{**account.snapshot.config.model_dump(), "account_id": "other"}))
        operation = lambda: StrategyOrderAdapter(rt, foreign)
    else:
        d = delivery()
        rt.process(d)
        adapter = StrategyOrderAdapter(rt, account)
        if kind == "competing_adapter": operation = lambda: StrategyOrderAdapter(rt, account)
        elif kind == "agent_decision": operation = lambda: adapter.submit(rt.snapshot.decisions[0])
        elif kind == "risk_override": operation = lambda: adapter.submit(close_quote(d), risk_decision="allow")
        elif kind == "quantity_override": operation = lambda: adapter.submit(close_quote(d), quantity=D("100"))
        elif kind == "exit": operation = lambda: adapter.submit(close_quote(d), action=SignalAction.EXIT_LONG)
        else: operation = lambda: adapter.submit(close_quote(d), side=OrderSide.SELL)
    with pytest.raises((PaperInputError, TypeError)): operation()
    assert account.snapshot == before
    assert account.events == ()


@pytest.mark.parametrize("kind", ["old_quote", "delayed_quote", "wrong_instrument"])
def test_invalid_close_quote_does_not_publish_submission_or_reservation(kind):
    rt, adapter, account, d, _, _ = ready()
    quote = close_quote(d)
    if kind == "old_quote": quote = quote.model_copy(update={"quote": quote.quote.model_copy(update={"timestamp": START})})
    elif kind == "delayed_quote": quote = quote.model_copy(update={"timestamp": quote.timestamp+MINUTE, "delivered_at": quote.delivered_at+MINUTE})
    else: quote = quote.model_copy(update={"quote": quote.quote.model_copy(update={"instrument_id": "other"})})
    before = account.snapshot, adapter.snapshot
    with pytest.raises(PaperInputError): adapter.submit(quote)
    assert (account.snapshot, adapter.snapshot) == before


def test_insufficient_prefunding_leaves_whole_entry_batch_unchanged():
    rt, adapter, account, d, _, _ = ready(capital="100")
    before = account.snapshot, adapter.snapshot
    with pytest.raises(PaperInputError): adapter.submit(close_quote(d))
    assert (account.snapshot, adapter.snapshot) == before
    assert account.events == () and not account._seen
    assert adapter.snapshot.inputs == ()


def test_price_gap_cancels_with_atomic_release_and_no_fill():
    rt, adapter, account, d, _, _ = ready(capital="300")
    adapter.submit(close_quote(d))
    records = adapter.process_open(opening(d, price="200"))
    assert not any(isinstance(r, FillRecord) for r in records)
    assert adapter.snapshot.state is OrderState.CANCELLED
    assert adapter.snapshot.events[-1].reason == "account_unfunded"
    assert account.snapshot.position is None
    assert account.snapshot.reserved_funds == 0
    assert account.snapshot.balance == D("300")
    assert account.events[-1].kind == "release"


@pytest.mark.parametrize("phase", ["reservation", "settlement"])
def test_account_publication_failure_rolls_back_all_owned_execution_state(monkeypatch, phase):
    rt, adapter, account, d, _, _ = ready()
    if phase == "settlement": adapter.submit(close_quote(d))
    before = account.snapshot, adapter.snapshot, account.events
    original = account._stage_indexes
    def fail(financial):
        original(financial)
        assert (account.snapshot, adapter.snapshot, account.events) == before
        raise RuntimeError("injected account publication failure")
    monkeypatch.setattr(account, "_stage_indexes", fail)
    operation = (lambda: adapter.submit(close_quote(d))) if phase == "reservation" else (lambda: adapter.process_open(opening(d)))
    with pytest.raises(RuntimeError): operation()
    assert (account.snapshot, adapter.snapshot, account.events) == before
    monkeypatch.setattr(account, "_stage_indexes", original)
    operation()
    assert adapter.snapshot.state is (OrderState.ACCEPTED if phase == "reservation" else OrderState.FILLED)


def test_risk_failure_is_a_recorded_denial_without_reservation_or_fill(monkeypatch):
    import quantlab.paper.orders as module
    rt, adapter, account, d, _, _ = ready()
    def fail(*args): raise RuntimeError("risk cannot approve")
    monkeypatch.setattr(module, "evaluate_entry_risk", fail)
    adapter.submit(close_quote(d))
    assert adapter.snapshot.state is OrderState.REJECTED
    assert account.events == () and account.snapshot.position is None
    with pytest.raises(PaperInputError): adapter.process_open(opening(d))


def test_no_client_can_settle_owned_fill_or_override_balances():
    from quantlab.paper import ApplyFill
    rt, adapter, account, d, _, _ = ready()
    adapter.submit(close_quote(d))
    records = adapter.process_open(opening(d))
    fill = next(r for r in records if isinstance(r, FillRecord))
    before = account.snapshot
    with pytest.raises(ValidationError): account.snapshot.balance = D("100000")
    item = ApplyFill(event_id="unauthorized-settlement", account_id="account", strategy_id=rt.config.strategy_id,
        sequence=before.last_input_sequence+1, timestamp=fill.timestamp, transaction_id=fill.order_id,
        causation_id=fill.event_id, order_id=fill.order_id, execution=fill.execution,
        source=fill.source, assumptions=fill.assumptions)
    with pytest.raises(PaperInputError): account.apply_trusted(item)
    assert account.snapshot == before


def test_order_identity_and_full_execution_are_decimal_context_independent():
    def execute():
        rt, adapter, account, d, _, _ = ready()
        adapter.submit(close_quote(d))
        adapter.process_open(opening(d))
        return rt.snapshot, adapter.snapshot, account.snapshot, account.events
    expected = execute()
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = context.traps[Rounded] = True
        assert execute() == expected
        assert context.prec == 2 and context.traps[Inexact] and context.traps[Rounded]
