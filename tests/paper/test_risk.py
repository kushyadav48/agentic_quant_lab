import pytest

from quantlab.backtesting import ExecutionCostConfig
from quantlab.paper import FillRecord, OrderSide, OrderState, PaperOrderKernel, RiskOutcome
from quantlab.risk import RiskAction, RiskConfig, RiskReason
from .helpers import D, config, market, submission


@pytest.mark.parametrize("side", tuple(OrderSide))
@pytest.mark.parametrize("policy,equity,peak,quantity,expected", [
    (RiskConfig(max_position_quantity=D("2")), "1000", "1000", "2", ()),
    (RiskConfig(max_position_quantity=D("1")), "1000", "1000", "2", (RiskReason.MAX_POSITION_QUANTITY,)),
    (RiskConfig(max_notional_exposure=D("200")), "1000", "1000", "2", ()),
    (RiskConfig(max_notional_exposure=D("199.99999999999999999999999999999999999")), "1000", "1000", "2", (RiskReason.MAX_NOTIONAL_EXPOSURE,)),
    (RiskConfig(max_equity_fraction=D("0.2")), "1000", "1000", "2", ()),
    (RiskConfig(max_equity_fraction=D("0.2")), "999.99999999999999999999999999999999999", "1000", "2", (RiskReason.MAX_EQUITY_FRACTION,)),
    (RiskConfig(minimum_equity=D("900")), "900", "1000", "2", (RiskReason.MINIMUM_EQUITY,)),
    (RiskConfig(max_drawdown_fraction=D("0.1")), "900", "1000", "2", (RiskReason.MAX_DRAWDOWN,)),
    (RiskConfig(max_drawdown_fraction=D("0.1")), "900.00000000000000000000000000000000001", "1000", "2", ()),
])
def test_existing_risk_thresholds_and_owned_context(side, policy, equity, peak, quantity, expected):
    kernel = PaperOrderKernel(config(flat_equity=D(equity), running_peak_equity=D(peak), risk=policy))
    kernel.process(market(ask="100"))
    records = kernel.process(submission(side=side, quantity=quantity))
    risk, = [r for r in records if isinstance(r, RiskOutcome)]
    assert risk.decision.reasons == expected
    assert risk.decision.action is (RiskAction.REJECT if expected else RiskAction.ALLOW)
    assert risk.decision.current_equity == D(equity)
    assert risk.decision.running_peak_equity == D(peak)
    assert risk.decision.reference_price == D("100")
    assert kernel.snapshot.state is (OrderState.REJECTED if expected else OrderState.ACCEPTED)
    assert not any(isinstance(r, FillRecord) for r in records)


def test_all_breaches_keep_phase11_stable_reason_order():
    policy = RiskConfig(max_position_quantity=D("1"), max_notional_exposure=D("199"),
        max_equity_fraction=D("0.1"), minimum_equity=D("900"), max_drawdown_fraction=D("0.1"))
    kernel = PaperOrderKernel(config(flat_equity=D("900"), risk=policy))
    kernel.process(market())
    risk, = [r for r in kernel.process(submission()) if isinstance(r, RiskOutcome)]
    assert risk.decision.reasons == tuple(RiskReason)


def test_risk_precedes_unrepresentable_hypothetical_execution():
    kernel = PaperOrderKernel(config(risk=RiskConfig(max_position_quantity=D("1")),
        costs=ExecutionCostConfig(slippage=D("1000"))))
    kernel.process(market())
    records = kernel.process(submission(side=OrderSide.SELL))
    assert kernel.snapshot.state is OrderState.REJECTED
    assert not any(isinstance(r, FillRecord) for r in records)
