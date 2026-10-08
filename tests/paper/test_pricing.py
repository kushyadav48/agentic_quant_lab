from decimal import Context, DefaultContext, Inexact, ROUND_DOWN, localcontext

import pytest

from quantlab.backtesting import ExecutionCostConfig
from quantlab.paper import FillRecord, KernelSnapshot, OrderSide, PaperPricingError
from quantlab.paper.pricing import price_quote
from .helpers import D, accepted, config, market, priced_config, submission


@pytest.mark.parametrize("side,price", [(OrderSide.BUY, "102.5"), (OrderSide.SELL, "99.5")])
def test_hand_computed_quote_pricing(side, price):
    kernel = accepted(cfg=priced_config(), side=side)
    fill, = [r for r in kernel.process(market(3)) if isinstance(r, FillRecord)]
    assert fill.execution.execution_price == D(price)
    assert fill.execution.costs.explicit_cost == D("1.2")
    assert fill.execution.costs.commission == D("0.2")
    assert fill.execution.costs.fees == D("1")
    assert fill.execution.costs.spread_cost == 0
    assert fill.execution.spread_adjustment == 0
    assert fill.execution.costs.slippage_cost == D("1")
    assert fill.source == market(3) and fill.assumptions == priced_config().costs
    assert kernel.snapshot.config.flat_equity == D("1000")


@pytest.mark.parametrize("side", tuple(OrderSide))
def test_zero_cost_preserves_exact_observed_side(side):
    kernel = accepted(side=side)
    exact = "100.123456789012345678901234567890123456789"
    fill, = [r for r in kernel.process(market(3, bid=exact, ask=exact)) if isinstance(r, FillRecord)]
    assert fill.execution.execution_price == D(exact)
    assert fill.execution.costs.total_cost == 0


def test_price_failure_is_atomic_and_does_not_consume_identity_or_clock():
    kernel = accepted(cfg=config(costs=ExecutionCostConfig(slippage=D("101"))), side=OrderSide.SELL)
    before = kernel.snapshot
    with pytest.raises(PaperPricingError):
        kernel.process(market(3))
    assert kernel.snapshot == before
    kernel.process(market(3, bid="200", ask="202"))
    assert kernel.snapshot.terminated


def test_below_precision_slippage_is_rejected_atomically():
    kernel = accepted(cfg=config(costs=ExecutionCostConfig(slippage=D("1e-100"))))
    before = kernel.snapshot
    with pytest.raises(PaperPricingError):
        kernel.process(market(3))
    assert kernel.snapshot == before


def test_pricing_cannot_accept_synthetic_spread_or_noncausal_observations():
    for event, costs in [
        (market(3), ExecutionCostConfig(spread=D("1"))),
        (market(1), ExecutionCostConfig()),
        (market(3).model_copy(update={"sequence": True}), ExecutionCostConfig()),
    ]:
        with pytest.raises(PaperPricingError):
            price_quote(submission(), event, costs)


def test_decimal_context_and_default_context_do_not_change_records_or_inputs():
    def execute():
        kernel = accepted(cfg=priced_config())
        kernel.process(market(3))
        return kernel.snapshot
    expected = execute()
    original = DefaultContext.copy()
    try:
        DefaultContext.prec = 2
        DefaultContext.rounding = ROUND_DOWN
        DefaultContext.traps[Inexact] = True
        with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as caller:
            caller.traps[Inexact] = True
            actual = execute()
            assert actual == expected
            assert actual.canonical_json() == expected.canonical_json()
            assert KernelSnapshot.model_validate_json(expected.canonical_json()) == expected
            assert caller.prec == 2 and caller.traps[Inexact]
    finally:
        DefaultContext.prec = original.prec
        DefaultContext.rounding = original.rounding
        DefaultContext.traps = original.traps.copy()


def test_fill_validator_rejects_fee_and_source_tampering():
    from pydantic import ValidationError
    from quantlab.paper import stable_id
    kernel = accepted(cfg=priced_config())
    fill, = [r for r in kernel.process(market(3)) if isinstance(r, FillRecord)]
    for changes in [
        {"source": market(3, ask="103")},
        {"assumptions": ExecutionCostConfig()},
        {"execution": fill.execution.model_copy(update={"costs": fill.execution.costs.model_copy(
            update={"commission": D("50")})})},
    ]:
        values = fill.model_dump(exclude={"event_id"})
        values.update(changes)
        values["event_id"] = stable_id("paper-event-v1", values)
        with pytest.raises(ValidationError):
            FillRecord(**values)
