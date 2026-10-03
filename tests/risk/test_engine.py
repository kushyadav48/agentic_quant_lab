"""Hand-calculated boundaries including excess precision beyond Decimal context."""
import pytest
from quantlab.risk import RiskAction as A, RiskConfig, RiskReason as R, RiskSide, evaluate_entry_risk
from tests.backtesting.helpers import D
from .test_models import context


@pytest.mark.parametrize("quantity, action", [("1", A.ALLOW), ("2", A.ALLOW), ("2.1", A.REJECT)])
def test_quantity_cap_allows_equality_and_rejects_full_excess(quantity, action):
    ctx = context().model_copy(update={"requested_quantity": D(quantity)})
    decision = evaluate_entry_risk(ctx, RiskConfig(max_position_quantity=D("2")))
    assert decision.action is action
    assert decision.approved_quantity == (D("0") if action is A.REJECT else D(quantity))
    assert decision.reasons == ((R.MAX_POSITION_QUANTITY,) if action is A.REJECT else ())


@pytest.mark.parametrize("price, action", [("99", A.ALLOW), ("100", A.ALLOW), ("100.01", A.REJECT),
    ("100.00000000000000000000000000000000001", A.REJECT)])
@pytest.mark.parametrize("side", tuple(RiskSide))
def test_notional_is_unsigned_reference_times_quantity_and_exact(price, action, side):
    ctx = context().model_copy(update={"reference_price": D(price), "side": side})
    decision = evaluate_entry_risk(ctx, RiskConfig(max_notional_exposure=D("200")))
    assert decision.action is action
    assert decision.reasons == ((R.MAX_NOTIONAL_EXPOSURE,) if action is A.REJECT else ())


@pytest.mark.parametrize("equity, action", [("1000", A.ALLOW), ("999.99", A.REJECT),
    ("999.99999999999999999999999999999999999", A.REJECT), ("0", A.REJECT), ("-10", A.REJECT)])
def test_equity_fraction_exact_equality_allowed_nonpositive_equity_blocked(equity, action):
    decision = evaluate_entry_risk(context().model_copy(update={"current_equity": D(equity)}),
        RiskConfig(max_equity_fraction=D("0.20")))
    assert decision.action is action
    assert decision.reasons == ((R.MAX_EQUITY_FRACTION,) if action is A.REJECT else ())


@pytest.mark.parametrize("equity, action", [("901", A.ALLOW), ("900", A.REJECT), ("899", A.REJECT)])
def test_minimum_equity_blocks_at_and_below_threshold(equity, action):
    decision = evaluate_entry_risk(context().model_copy(update={"current_equity": D(equity)}),
        RiskConfig(minimum_equity=D("900")))
    assert decision.action is action
    assert decision.reasons == ((R.MINIMUM_EQUITY,) if action is A.REJECT else ())


@pytest.mark.parametrize("equity, action", [("1000", A.ALLOW), ("901", A.ALLOW),
    ("900.00000000000000000000000000000000001", A.ALLOW),
    ("900", A.REJECT), ("850", A.REJECT), ("0", A.REJECT), ("-1", A.REJECT)])
def test_drawdown_is_peak_relative_and_blocks_exact_loss_threshold(equity, action):
    decision = evaluate_entry_risk(context().model_copy(update={"current_equity": D(equity)}),
        RiskConfig(max_drawdown_fraction=D("0.10")))
    assert decision.action is action
    assert decision.reasons == ((R.MAX_DRAWDOWN,) if action is A.REJECT else ())


def test_drawdown_uses_running_peak_not_initial_capital():
    ctx = context().model_copy(update={"running_peak_equity": D("1200")})
    assert evaluate_entry_risk(ctx, RiskConfig(max_drawdown_fraction=D("0.10"))).action is A.REJECT


def test_all_breaches_retained_in_stable_order_and_no_resizing():
    cfg = RiskConfig(max_position_quantity=D("1"), max_notional_exposure=D("199"),
        max_equity_fraction=D("0.10"), minimum_equity=D("900"), max_drawdown_fraction=D("0.10"))
    ctx = context().model_copy(update={"current_equity": D("900")})
    expected = evaluate_entry_risk(ctx, cfg)
    assert expected.action is A.REJECT and expected.approved_quantity == 0
    assert expected.reasons == (R.MAX_POSITION_QUANTITY, R.MAX_NOTIONAL_EXPOSURE,
        R.MAX_EQUITY_FRACTION, R.MINIMUM_EQUITY, R.MAX_DRAWDOWN)
    assert expected.requested_quantity == D("2")
    assert expected.signal_time == ctx.signal_time and expected.execution_time == ctx.execution_time
    assert expected.current_equity == D("900") and expected.running_peak_equity == D("1000")
    assert expected.reference_price == D("100") and expected.side is RiskSide.LONG
    assert evaluate_entry_risk(ctx, cfg).model_dump_json() == expected.model_dump_json()
