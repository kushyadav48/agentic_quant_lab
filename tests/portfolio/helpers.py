"""Portfolio fixtures obtain attribution and economics from real paper owners."""
from decimal import Decimal as D
from quantlab.backtesting import ExecutionCostConfig
from quantlab.paper import AdvancedReplayConfig, PaperSession
from quantlab.portfolio import (EnrollMember, ObserveSession, Portfolio, PortfolioConfig,
    PortfolioMember, RiskBudget)
from quantlab.strategies import Direction, StrategySpecification
from tests.backtesting.helpers import CONFIG, MINUTE, approve, strategy
from tests.paper.strategy_helpers import setup, delivery, close_quote, opening
from tests.paper.test_sessions import SOURCE, T, command, event
from quantlab.paper import ReplayConfig, StaleFeedPolicy


def member_owner(name="a", *, strategy_id=None, version=1, direction=Direction.LONG,
                 capital="1000", encumbered="500", gross="500", advanced=False, costs=None):
    original = strategy(no_exit=True, direction=direction)
    spec = approve(StrategySpecification(strategy_id=strategy_id or name, version=version,
        content=original.content))
    research = CONFIG.model_copy(update={"execution_costs": costs or ExecutionCostConfig()})
    args, _, _ = setup(spec, capital=capital, research_config=research)
    account = args["config"].account.model_copy(update={"account_id": f"account:{name}"})
    cfg = args["config"].model_copy(update={"account": account, "session_id": f"session:{name}"})
    cls = AdvancedReplayConfig if advanced else ReplayConfig
    cfg = cls(strategy=cfg, sources=(SOURCE,), maximum_inputs=1000,
        stale=StaleFeedPolicy(policy_id="age", version=1, maximum_age=10*MINUTE))
    owner = PaperSession(cfg, **{k: args[k] for k in ("strategy", "policy", "eligibility", "evidence")})
    member = PortfolioMember(member_id=name, config=cfg, strategy=spec,
        admission=owner.snapshot.runtime.admission,
        risk=RiskBudget(maximum_encumbered=D(encumbered), maximum_gross_exposure=D(gross)))
    return member, owner


def portfolio(*, capital="3000", encumbered="1500", gross="1500", age=10*MINUTE, **changes):
    return Portfolio(PortfolioConfig(portfolio_id="portfolio", denomination="USD", total_capital=D(capital),
        timestamp=T, maximum_valuation_age=age,
        risk=RiskBudget(maximum_encumbered=D(encumbered), maximum_gross_exposure=D(gross)), **changes))


def enroll(p, member, **changes):
    body = dict(operation_id=f"enroll:{member.member_id}", member=member,
        sequence=p.snapshot.sequence+1, timestamp=p.snapshot.timestamp)
    body.update(changes)
    item = EnrollMember(**body)
    return item, p.process(item)


def observe(p, member, r, **changes):
    body = dict(operation_id=f"observe:{member.member_id}:{r.sequence}", member_id=member.member_id,
        record=r, sequence=p.snapshot.sequence+1, timestamp=max(p.snapshot.timestamp, r.timestamp))
    body.update(changes)
    item = ObserveSession(**body)
    return item, p.process(item)


def entry_inputs(*, short=False):
    bar = delivery(close=99 if short else 101, sequence=2)
    return (command("start"), event(bar), event(close_quote(bar, sequence=3)),
        event(opening(bar, sequence=5)))


def execute(p, member, owner, *, short=False):
    for item in entry_inputs(short=short):
        observe(p, member, owner.process(item))
