"""Explicit human-reviewed advanced opt-in on top of actual retained research."""
from decimal import Decimal as D
from quantlab.paper import (
    AdvancedEntryPolicy, AdvancedEntryApproval, AdvancedEligibilityPolicy,
    AdvancedEligibilityDecision, AdvancedStrategySessionConfig, AdvancedEntryReplayConfig,
    PaperSession, StaleFeedPolicy, EntryCancellationCommand, stable_id,
)
from quantlab.paper.strategy_models import record
from tests.paper.strategy_helpers import setup, delivery, close_quote, opening
from tests.paper.test_sessions import SOURCE, T, command, event
from tests.backtesting.helpers import MINUTE, CONFIG
from quantlab.backtesting import ExecutionCostConfig

COSTS = ExecutionCostConfig(commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1"))


def configuration(*, kind="limit", budget=D("1"), tif="gtc", capital="1000",
                  quantity=D("2"), prices=None, spec=None, risk=None, maximum_inputs=128):
    research = CONFIG.model_copy(update={"quantity": quantity, "execution_costs": COSTS,
        **({} if risk is None else {"risk": risk})})
    args, _, _ = setup(spec=spec, capital=capital, research_config=research)
    params = {"market": {}, "limit": {"limit_price": D("100")},
        "stop_market": {"stop_price": D("103")},
        "stop_limit": {"stop_price": D("103"), "limit_price": D("104")}}[kind]
    if prices is not None:
        params = prices
    entry = AdvancedEntryPolicy(policy_id="reviewed-entry", version=1, order_type=kind,
        time_in_force=tif, liquidity_per_observation=budget, maximum_age=10*MINUTE,
        maximum_inputs=maximum_inputs, **params)
    body = args["config"].model_dump(mode="python") | {"schema_version": 2, "entry_policy": entry}
    authorization = stable_id("paper-advanced-entry-configuration-v2", body)
    approval = record(AdvancedEntryApproval, **{k: body[k] for k in (
        "strategy_id", "strategy_version", "strategy_digest")}, configuration_digest=authorization,
        reviewer="human:advanced-execution", timestamp=T-MINUTE,
        reason_reference="human:reviewed-resting-entry")
    config = AdvancedStrategySessionConfig(**body, execution_approval=approval)
    pol = AdvancedEligibilityPolicy(**args["policy"].model_dump(exclude={"execution_policy"}))
    eligible = record(AdvancedEligibilityDecision,
        **args["eligibility"].model_dump(exclude={"record_id", "schema_version", "policy_digest"}),
        policy_digest=pol.digest, execution_approval_id=approval.record_id,
        configuration_digest=authorization)
    owners = {k: args[k] for k in ("strategy", "evidence")}
    owners.update(policy=pol, eligibility=eligible)
    replay = AdvancedEntryReplayConfig(strategy=config,
        stale=StaleFeedPolicy(policy_id="fresh", version=1, maximum_age=entry.maximum_age),
        sources=(SOURCE,), maximum_inputs=500, liquidity_per_observation=budget)
    return replay, owners


def session(**kwargs):
    config, owners = configuration(**kwargs)
    return PaperSession(config, **owners), owners


def prefix(price="101", close=101):
    close = delivery(sequence=2, close=close)
    return (command("start"), event(close), event(close_quote(close, sequence=3)),
        event(opening(close, sequence=5, price=price)))


def quote(n, price="100", time=None, *, age=None, identity=None):
    time = T+MINUTE+(n-5)*MINUTE if time is None else time
    observed = time if age is None else time-age
    source = close_quote(delivery(), sequence=n, event_id=identity or f"entry-quote-{n}", bid=price, ask=price)
    source = source.model_copy(update={"timestamp": time, "delivered_at": time,
        "quote": source.quote.model_copy(update={"timestamp": observed, "available_at": observed})})
    return event(source)


def cancel(owner, n, action="request_cancel_entry", time=None):
    return EntryCancellationCommand(command_id=f"{action}-{n}", sequence=n,
        timestamp=time or T+(n-4)*MINUTE, action=action,
        intent_id=owner.snapshot.runtime.intent.record_id, reason_reference="human:cancel")
