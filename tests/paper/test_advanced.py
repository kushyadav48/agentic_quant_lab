"""Phase 18F: causal matching, exact partial settlement and controlled exits."""
from datetime import timedelta
from decimal import localcontext, ROUND_DOWN

import pytest
from pydantic import ValidationError

from quantlab.backtesting import ExecutionCostConfig, PositionSide
from quantlab.paper import (AdvancedKernelConfig, AdvancedOrderSubmission, AdvancedFillRecord,
    CancellationAcknowledgement, OrderActivation, StopTrigger, OrderState as S,
    PaperAccount, PaperOrderKernel, PaperInputError, PaperIdentityConflict, RiskOutcome,
    KernelSnapshot)
from quantlab.persistence.contracts import decode, PersistenceError
from .helpers import D, START, SECOND, config, market, submission, cancel
from .test_accounts import cfg, opened, COSTS


def advanced_command(**kw):
    base = submission(**{k: kw.pop(k) for k in tuple(kw) if k in
        ("sequence", "side", "quantity", "command_id", "timestamp", "causation_id", "instrument_id")})
    return AdvancedOrderSubmission(**base.model_dump(exclude={"kind", "order_type", "time_in_force"}), **kw)


def kernel(*, budget=None, costs=None, risk=None, maximum_age=SECOND, maximum_inputs=128, **command):
    k = PaperOrderKernel(AdvancedKernelConfig(schema_version=2, **config(
        **({} if costs is None else {"costs": costs}), **({} if risk is None else {"risk": risk})).model_dump(),
        liquidity_per_observation=None if budget is None else D(budget), maximum_age=maximum_age,
        maximum_inputs=maximum_inputs))
    k.process(market())
    k.process(advanced_command(**command))
    return k


def quote(n, bid="100", ask="102", **kw):
    return market(n, bid=bid, ask=ask, observed=START+n*SECOND, **kw)


def ack(k, n, **kw):
    return CancellationAcknowledgement(sequence=n, timestamp=kw.pop("timestamp", k.snapshot.timestamp),
        command_id=kw.pop("command_id", "ack"), causation_id=k.snapshot.pending_cancellation.request_id,
        order_id=k.snapshot.order_id, **kw)


def fills(records):
    return [r for r in records if isinstance(r, AdvancedFillRecord)]


@pytest.mark.parametrize("kind,params", [("market", {}), ("limit", {"limit_price": D("101")}),
    ("stop_market", {"stop_price": D("103")}),
    ("stop_limit", {"stop_price": D("103"), "limit_price": D("104")})])
def test_supported_strict_contracts_and_legacy_rejection(kind, params):
    command = advanced_command(order_type=kind, **params)
    assert decode(AdvancedOrderSubmission, command.canonical_json()) == command
    with pytest.raises(ValidationError):
        submission(order_type=kind if kind != "market" else "stop_market")
    with pytest.raises(PersistenceError):
        decode(AdvancedOrderSubmission, command.canonical_json().replace('"schema_version":2', '"schema_version":3'))


@pytest.mark.parametrize("changes", [dict(order_type="limit"), dict(order_type="stop_market"),
    dict(order_type="market", limit_price=D("1")), dict(order_type="limit", limit_price=D("0")),
    dict(order_type="stop_market", stop_price=D("NaN")), dict(time_in_force="day"),
    dict(time_in_force="gtd"), dict(time_in_force="fok"), dict(quantity="0"),
    dict(reduce_only=True), dict(position_id="x"), dict(protective_role="stop_loss"),
    dict(order_type="stop_market", stop_price=D("100"), time_in_force="ioc")])
def test_invalid_parameters(changes):
    with pytest.raises((ValidationError, ValueError)):
        advanced_command(**changes)


@pytest.mark.parametrize("buy", [True, False])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_limit_side_comparisons_and_slippage_cap(buy, offset):
    from quantlab.paper import OrderSide
    price = D("102") if buy else D("100")
    limit = price + offset
    k = kernel(order_type="limit", limit_price=limit,
        side=OrderSide.BUY if buy else OrderSide.SELL,
        maximum_age=10*SECOND, costs=ExecutionCostConfig(slippage=D("2")))
    output = k.process(quote(3))
    executable = offset >= 0 if buy else offset <= 0
    assert bool(fills(output)) == executable
    if executable:
        f = fills(output)[0]
        assert f.execution.reference_price == price
        assert f.execution.execution_price == limit
        assert f.execution.slippage_adjustment == abs(D(offset))
        assert f.execution.costs.spread_cost == 0


@pytest.mark.parametrize("buy", [True, False])
@pytest.mark.parametrize("kind", ["stop_market", "stop_limit"])
def test_stop_requires_trigger_then_later_quote_with_gap_policy(buy, kind):
    from quantlab.paper import OrderSide
    stop = D("105") if buy else D("95")
    limit = D("106") if buy else D("94")
    k = kernel(order_type=kind, stop_price=stop, side=OrderSide.BUY if buy else OrderSide.SELL,
        maximum_age=10*SECOND, **({"limit_price": limit} if kind == "stop_limit" else {}))
    assert not k.process(quote(3))
    triggering = quote(4, bid="93", ask="107")
    output = k.process(triggering)
    assert len([r for r in output if isinstance(r, StopTrigger)]) == 1 and not fills(output)
    assert k.process(triggering) == output
    later = k.process(quote(5, bid="93", ask="107"))
    assert bool(fills(later)) == (kind == "stop_market")
    if kind == "stop_limit":
        assert k.snapshot.state is S.ACCEPTED
        assert fills(k.process(quote(6, bid="94", ask="106")))
    f = next(r for r in k.snapshot.events if isinstance(r, AdvancedFillRecord))
    t = next(r for r in k.snapshot.events if isinstance(r, StopTrigger))
    assert f.source.sequence > t.source.sequence
    assert len({f.event_id, f.activation_id, f.trigger_id, f.submission.command_id}) == 4
    assert decode(KernelSnapshot, k.snapshot.canonical_json()) == k.snapshot


def test_partial_budget_same_observation_and_no_overfill():
    k = kernel(budget="1", quantity="3", maximum_age=10*SECOND)
    first = quote(3)
    result = k.process(first)
    assert k.snapshot.filled_quantity == 1 and k.snapshot.remaining_quantity == 2
    assert k.snapshot.state is S.PARTIALLY_FILLED
    assert k.process(first) == result
    # Redelivery under a different ID still uses the same quote budget.
    assert not fills(k.process(market(4, event_id="redelivered", observed=first.quote.timestamp)))
    assert fills(k.process(quote(5)))[0].cumulative_quantity == 2
    assert fills(k.process(quote(6)))[0].remaining_quantity == 0
    assert k.snapshot.filled_quantity == 3 and k.snapshot.state is S.FILLED
    assert not k.process(quote(7))
    with pytest.raises(PaperInputError):
        k.process(advanced_command(sequence=8, command_id="reopen"))


@pytest.mark.parametrize("budget", [None, "1"])
def test_no_quote_liquidity_inference(budget):
    k = kernel(budget=budget, quantity="3", maximum_age=10*SECOND)
    f = fills(k.process(quote(3)))[0]
    assert f.execution.quantity == (3 if budget is None else 1)
    assert f.liquidity_policy == ("full-fill-assumption-v1" if budget is None else "simulated-per-observation-v2")


@pytest.mark.parametrize("budget,limit,expected", [(None, "99", D("0")), ("1", "102", D("1"))])
def test_ioc_first_eligible_attempt_expires_remainder(budget, limit, expected):
    k = kernel(budget=budget, quantity="3", order_type="limit", limit_price=D(limit),
        time_in_force="ioc", maximum_age=10*SECOND)
    k.process(quote(3))
    assert k.snapshot.state is S.EXPIRED and k.snapshot.filled_quantity == expected
    assert k.snapshot.terminated
    assert not k.process(quote(4))


@pytest.mark.parametrize("race", ["ack_first", "fill_first"])
def test_cancel_request_partial_fill_race_and_idempotency(race):
    k = kernel(budget="1", maximum_age=10*SECOND)
    k.process(quote(3))
    req = cancel(k, 4, timestamp=k.snapshot.timestamp)
    receipt = k.process(req)
    assert k.process(req) == receipt and k.snapshot.pending_cancellation is not None
    if race == "ack_first":
        response = ack(k, 5)
        result = k.process(response)
        assert k.snapshot.state is S.CANCELLED and k.snapshot.filled_quantity == 1
        assert not k.process(quote(6))
    else:
        k.process(quote(5))
        response = CancellationAcknowledgement(sequence=6, timestamp=k.snapshot.timestamp,
            command_id="ack", causation_id=req.command_id, order_id=k.snapshot.order_id)
        result = k.process(response)
        assert not result[-1].cancelled and k.snapshot.state is S.FILLED
    assert k.process(response) == result


def owned_entry(*, budget="1", quantity="2", costs=COSTS, capital="1000", tif="gtc"):
    a = PaperAccount(cfg(starting_capital=D(capital)))
    k = a.create_advanced_order_kernel(session_id="entry", strategy_id="alpha", costs=costs,
        liquidity_per_observation=None if budget is None else D(budget), maximum_age=10*SECOND)
    k.process(market())
    k.process(advanced_command(quantity=quantity, time_in_force=tif))
    a.reserve_order(k, event_id="reserve", sequence=1, timestamp=START)
    return a, k


def test_exact_partial_entry_accounting_and_reservation_consumption():
    a, k = owned_entry()
    assert a.snapshot.reserved_funds == D("206.2")
    k.process(quote(3))
    p = a.snapshot
    assert (p.position.quantity, p.position_collateral, p.fees_paid) == (D("1"), D("102"), D("1.1"))
    assert p.reserved_funds == D("103.1") and p.available_funds == D("793.8")
    assert p.equity == D("996.9")
    k.process(quote(4))
    p = a.snapshot
    assert p.position.quantity == 2 and p.position.entry_basis == D("102")
    assert p.fees_paid == D("2.2") and p.reserved_funds == 0
    assert p.available_funds == D("793.8") and p.equity == D("993.8")
    assert k.snapshot.cumulative_costs.fees == 2
    assert k.snapshot.cumulative_costs.commission == D("0.2")


@pytest.mark.parametrize("tif", ["gtc", "ioc"])
def test_partial_cancellation_or_ioc_releases_only_remaining_collateral(tif):
    a, k = owned_entry(tif=tif)
    k.process(quote(3))
    if tif == "gtc":
        k.process(cancel(k, 4, timestamp=k.snapshot.timestamp))
        assert a.snapshot.reserved_funds > 0
        k.process(ack(k, 5))
    assert a.snapshot.position.quantity == 1 and a.snapshot.fees_paid == D("1.1")
    assert a.snapshot.reserved_funds == 0 and a.snapshot.available_funds == D("896.9")


def test_funding_gap_rejection_after_partial_preserves_committed_fill():
    a, k = owned_entry(capital="210")
    k.process(quote(3))
    old = a.snapshot
    output = k.process(quote(4, bid="1000", ask="1002"))
    assert not fills(output) and k.snapshot.state is S.CANCELLED
    assert a.snapshot.position == old.position and a.snapshot.fees_paid == old.fees_paid
    assert a.snapshot.reserved_funds == 0 and k.snapshot.filled_quantity == 1


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("quantity", ["1", "2"])
def test_reduce_only_long_short_financials(short, quantity):
    a = opened(short=short)
    p = a.snapshot.position
    from quantlab.paper import OrderSide
    k = a.create_advanced_order_kernel(session_id="exit", strategy_id="alpha", reduce_only=True,
        costs=COSTS, maximum_age=10*SECOND)
    k.process(market(3))
    command = advanced_command(sequence=4, quantity=quantity,
        side=OrderSide.BUY if short else OrderSide.SELL, reduce_only=True,
        position_id=p.entry_transaction_id, causation_id="quote-3")
    k.process(command)
    output = k.process(quote(5, bid="110", ask="112"))
    assert fills(output) and not any(isinstance(r, RiskOutcome) for r in output)
    expected_gross = D("-12")*D(quantity) if short else D("8")*D(quantity)
    assert a.snapshot.realized_pnl == expected_gross
    assert a.snapshot.position.quantity == 2-D(quantity)
    assert a.snapshot.position_collateral == (D("100") if short else D("102"))*(2-D(quantity))
    expected_fees = D("1.2") + D("1") + D("0.1")*D(quantity)
    assert a.snapshot.fees_paid == expected_fees
    assert a.snapshot.balance == 1000+expected_gross-expected_fees
    before = a.snapshot
    assert k.process(command)
    assert a.snapshot is before


@pytest.mark.parametrize("bad", ["quantity", "side", "owner", "position"])
def test_reduce_only_rejects_oversize_direction_and_ownership(bad):
    from quantlab.paper import OrderSide
    a = opened()
    if bad == "owner":
        with pytest.raises(PaperInputError):
            a.create_advanced_order_kernel(session_id="exit", strategy_id="wrong", reduce_only=True)
        return
    k = a.create_advanced_order_kernel(session_id="exit", strategy_id="alpha", reduce_only=True)
    k.process(market(3))
    changes = dict(sequence=4, side=OrderSide.SELL, quantity="1", reduce_only=True,
        position_id=a.snapshot.position.entry_transaction_id, causation_id="quote-3")
    changes.update({"quantity": "3"} if bad == "quantity" else {"side": OrderSide.BUY} if bad == "side" else {"position_id": "wrong"})
    before = a.snapshot, k.snapshot
    with pytest.raises(PaperInputError):
        k.process(advanced_command(**changes))
    assert (a.snapshot, k.snapshot) == before


def test_exact_weighted_partial_basis_and_unrepresentable_average_rejected():
    a, k = owned_entry(quantity="3", capital="1000")
    k.process(quote(3))
    k.process(quote(4, bid="101", ask="103"))
    assert a.snapshot.position.entry_basis == D("102.5")
    before = a.snapshot
    # (102+103+104)/3 is exactly representable.
    k.process(quote(5, bid="102", ask="104"))
    assert a.snapshot.position.entry_basis == D("103")
    a, k = owned_entry(quantity="3")
    k.process(quote(3))
    k.process(quote(4))
    prior = a.snapshot
    k.process(quote(5, bid="99", ask="101"))
    assert k.snapshot.state is S.CANCELLED and k.snapshot.filled_quantity == 2
    assert a.snapshot.position == prior.position and a.snapshot.fees_paid == prior.fees_paid
    assert not a.snapshot.reservations


def test_stale_delayed_and_equal_timestamp_sequencing():
    k = kernel(maximum_age=SECOND)
    stale = market(3, observed=START, delivered=START+2*SECOND)
    assert not k.process(stale)
    assert k.snapshot.state is S.ACCEPTED
    assert fills(k.process(market(4, observed=START+2*SECOND)))
    k = kernel(order_type="stop_market", stop_price=D("102"))
    assert isinstance(k.process(market(3))[-1], StopTrigger)
    assert fills(k.process(market(4)))  # sequence proves causality at equal UTC time


def test_risk_revalidation_and_decimal_context_independence():
    from quantlab.risk import RiskConfig
    k = kernel(risk=RiskConfig(max_notional_exposure=D("210")), maximum_age=10*SECOND)
    k.process(quote(3, ask="112"))
    assert k.snapshot.state is S.CANCELLED and not k.snapshot.filled_quantity
    def execute():
        a, k = owned_entry()
        k.process(quote(3)); k.process(quote(4))
        return a.snapshot.canonical_json(), k.snapshot.canonical_json()
    expected = execute()
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_DOWN
        assert execute() == expected


def test_capacity_allows_recorded_cancel_and_ack():
    k = kernel(maximum_inputs=3, order_type="limit", limit_price=D("99"), maximum_age=10*SECOND)
    k.process(quote(3))
    with pytest.raises(PaperInputError):
        k.process(quote(4))
    k.process(cancel(k, 4, timestamp=k.snapshot.timestamp))
    k.process(ack(k, 5))
    assert k.snapshot.state is S.CANCELLED


@pytest.mark.parametrize("stage", ["indexes", "allocation"])
def test_partial_settlement_failure_is_atomic_and_retryable(monkeypatch, stage):
    a, k = owned_entry()
    k.process(quote(3))
    before = a.snapshot, k.snapshot, a.events, dict(a._seen), set(a._execution_ids)
    def fail(*args, **kw):
        raise RuntimeError("injected allocation failure")
    with monkeypatch.context() as patch:
        if stage == "indexes":
            original = a._stage_indexes
            def staged(values):
                original(values)
                fail()
            patch.setattr(a, "_stage_indexes", staged)
        else:
            import quantlab.paper.accounts as module
            patch.setattr(module, "_AccountPublication", fail)
        with pytest.raises(RuntimeError):
            k.process(quote(4))
    assert (a.snapshot, k.snapshot, a.events, dict(a._seen), set(a._execution_ids)) == before
    k.process(quote(4))
    assert a.snapshot.fees_paid == D("2.2")


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("role", ["stop_loss", "take_profit"])
def test_single_protective_child_long_short_partial_and_no_sibling(short, role):
    from quantlab.paper import OrderSide
    from quantlab.risk import RiskConfig
    a = opened(short=short)
    position = a.snapshot.position
    k = a.create_advanced_order_kernel(session_id="protection", strategy_id="alpha", reduce_only=True,
        liquidity_per_observation=D("1"), costs=COSTS, maximum_age=10*SECOND,
        risk=RiskConfig(minimum_equity=D("2000"), max_position_quantity=D("0.5")))
    k.process(market(3))
    params = dict(order_type="stop_market", stop_price=D("105") if short else D("95")) if role == "stop_loss" else (
        dict(order_type="limit", limit_price=D("95") if short else D("105")))
    k.process(advanced_command(sequence=4, side=OrderSide.BUY if short else OrderSide.SELL,
        reduce_only=True, position_id=position.entry_transaction_id, causation_id="quote-3",
        protective_role=role, **params))
    with pytest.raises(PaperInputError):
        a.create_advanced_order_kernel(session_id="sibling", strategy_id="alpha", reduce_only=True)
    bid, ask = ("110", "112") if (short and role == "stop_loss" or not short and role == "take_profit") else ("90", "92")
    first = k.process(quote(5,bid,ask))
    if role == "stop_loss":
        assert not fills(first)
        first = k.process(quote(6,bid,ask))
    assert fills(first)[0].execution.quantity == 1 and a.snapshot.position.quantity == 1
    last = k.process(quote(7,bid,ask))
    assert fills(last)[0].remaining_quantity == 0 and a.snapshot.position.quantity == 0
    assert a.snapshot.fees_paid == D("3.4") and a.snapshot.position_collateral == 0


def test_account_shared_quote_budget_across_sequential_reductions():
    from quantlab.paper import OrderSide
    a = opened()
    def child(name, seed_seq, command_seq):
        k = a.create_advanced_order_kernel(session_id=name, strategy_id="alpha", reduce_only=True,
            liquidity_per_observation=D("1"), maximum_age=10*SECOND)
        k.process(market(seed_seq, observed=START+SECOND))
        k.process(advanced_command(sequence=command_seq, quantity="1", side=OrderSide.SELL,
            reduce_only=True, position_id=a.snapshot.position.entry_transaction_id,
            causation_id=f"quote-{seed_seq}", command_id=name, timestamp=START+SECOND))
        return k
    first = child("first",3,4)
    observation = market(5, observed=START+SECOND)
    assert fills(first.process(observation))
    second = child("second",6,7)
    assert not fills(second.process(market(8, observed=START+SECOND)))
    assert a.snapshot.position.quantity == 1
    assert fills(second.process(market(9, observed=START+2*SECOND)))
    assert a.snapshot.position.quantity == 0


@pytest.mark.parametrize("change", ["identity", "invalid", "instrument", "future"])
def test_advanced_identity_conflicts_and_failed_inputs_leave_state(change):
    k = kernel(maximum_age=10*SECOND)
    item = quote(3)
    k.process(item)
    before = k.snapshot
    changed = item.model_copy(update={"quote": item.quote.model_copy(update={"ask": D("104")})})
    if change == "invalid":
        changed = item.model_copy(update={"sequence": -1})
    elif change == "instrument":
        changed = quote(4).model_copy(update={"quote": item.quote.model_copy(update={"instrument_id": "wrong"})})
    elif change == "future":
        changed = quote(4).model_copy(update={"timestamp": START})
    with pytest.raises(PaperIdentityConflict if change in ("identity", "invalid") else PaperInputError):
        k.process(changed)
    assert k.snapshot is before


def test_independent_sessions_and_future_suffix_do_not_rewrite_fills():
    first = kernel(budget="1", maximum_age=10*SECOND)
    second = kernel(budget="1", maximum_age=10*SECOND)
    observation = quote(3)
    assert first.process(observation) == second.process(observation)
    prior = first.snapshot
    first.process(quote(4, bid="10000", ask="10002"))
    assert prior.events == first.snapshot.events[:len(prior.events)]
    assert second.snapshot == prior


@pytest.mark.parametrize("wire_change", ["duplicate", "float", "extra", "noncanonical"])
def test_advanced_strict_codec(wire_change):
    wire = advanced_command(order_type="limit", limit_price=D("101")).canonical_json()
    if wire_change == "duplicate":
        wire = wire.replace('"quantity":"2"','"quantity":"2","quantity":"2"')
    elif wire_change == "float":
        wire = wire.replace('"quantity":"2"','"quantity":2.0')
    elif wire_change == "extra":
        wire = wire[:-1]+',"extra":true}'
    else:
        wire = wire.replace('"quantity":"2"','"quantity":"2.0"')
    with pytest.raises(PersistenceError):
        decode(AdvancedOrderSubmission,wire)



def test_snapshot_config_digest_once_and_integrity_preserved(monkeypatch):
    import quantlab.paper.models as models
    k = kernel(budget="1", quantity="4", maximum_age=10*SECOND)
    k.process(quote(3)); k.process(quote(4))
    calls=[]
    original=models.stable_id
    def counted(namespace,value):
        if namespace == "paper-config-v1":
            calls.append(namespace)
        return original(namespace,value)
    with monkeypatch.context() as patch:
        patch.setattr(models,"stable_id",counted)
        assert KernelSnapshot.model_validate(k.snapshot) == k.snapshot
    # Lazy consumer export and explicit revalidation each derive one digest.
    assert calls == ["paper-config-v1", "paper-config-v1"]
    # Changing the configuration still fails the journal binding check.
    values=k.snapshot.model_dump()
    values["config"]["session_id"]="corrupt"
    with pytest.raises(ValidationError):
        KernelSnapshot(**values)



def test_full_fill_policy_cannot_reuse_previously_budgeted_quote():
    from quantlab.paper import OrderSide
    a = opened()
    def child(name, n, budget):
        k=a.create_advanced_order_kernel(session_id=name,strategy_id="alpha",reduce_only=True,
            liquidity_per_observation=budget,maximum_age=10*SECOND)
        k.process(market(n,observed=START+SECOND))
        k.process(advanced_command(sequence=n+1,quantity="1",side=OrderSide.SELL,
            command_id=name,causation_id=f"quote-{n}",timestamp=START+SECOND,
            reduce_only=True,position_id=a.snapshot.position.entry_transaction_id))
        return k
    first=child("first",3,D("1"))
    first.process(market(5,observed=START+SECOND))
    second=child("second",6,None)
    before=a.snapshot,second.snapshot
    with pytest.raises(PaperInputError,match="liquidity policy"):
        second.process(market(8,observed=START+SECOND))
    assert (a.snapshot,second.snapshot)==before
    second.process(market(9,observed=START+2*SECOND))
    assert a.snapshot.position.quantity == 0



def test_partial_entry_risk_uses_current_quote_marked_equity():
    from quantlab.risk import RiskConfig
    a=PaperAccount(cfg())
    k=a.create_advanced_order_kernel(session_id="entry",strategy_id="alpha",costs=COSTS,
        liquidity_per_observation=D("1"),maximum_age=10*SECOND,
        risk=RiskConfig(minimum_equity=D("950")))
    k.process(market());k.process(advanced_command())
    a.reserve_order(k,event_id="reserve",sequence=1,timestamp=START)
    k.process(quote(3))
    prior=a.snapshot
    outcome=k.process(quote(4,bid="40",ask="42"))
    risk=next(r for r in outcome if isinstance(r,RiskOutcome))
    assert risk.decision.current_equity == D("936.9")  # 998.9 balance + (40-102)*1
    assert k.snapshot.state is S.CANCELLED and not fills(outcome)
    assert a.snapshot.position == prior.position and a.snapshot.fees_paid == D("1.1")
    assert a.snapshot.reserved_funds == 0 and k.snapshot.filled_quantity == 1
