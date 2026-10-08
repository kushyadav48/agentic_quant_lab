"""Synthetic Phase 18C fixtures; real engines produce all retained report artifacts."""
from datetime import timedelta
from decimal import Decimal as D

from quantlab.backtesting import run_backtest
from quantlab.data import MarketQuote, PriceType, Timeframe
from quantlab.paper import (
    AccountConfig, BarCloseDelivery, DatasetVersion, EligibilityDecision,
    EligibilityPolicy, EvidenceReference, MarketDelivery, OpeningDelivery,
    PaperAccount, ResearchEvidence, ResearchEvidenceStore, StrategyOrderAdapter,
    StrategyRuntime, StrategySessionConfig, stable_id,
)
from quantlab.paper.admission import binding
from quantlab.paper.strategy_models import record
from quantlab.validation import (
    HoldoutConfig, RobustnessCandidate, ValidationWindow, WalkForwardConfig,
    run_holdout, run_parameter_robustness, run_walk_forward,
)
from tests.backtesting.helpers import CONFIG, INSTRUMENT, START, MINUTE, bars, strategy


def policy(**changes):
    body = dict(policy_id="reviewed-fixture-policy", version=1,
        rationale_reference="human:research-requirements-v1", oos_requirement="either",
        minimum_oos_observations=1, minimum_robustness_candidates=1,
        minimum_oos_return=None, minimum_oos_drawdown=None)
    body.update(changes)
    return EligibilityPolicy(**body)


def bundle(spec=None, *, walk=False, report_changes=None, research_config=CONFIG):
    spec = strategy(no_exit=True) if spec is None else spec
    series = tuple(b.model_copy(update={"price_type": PriceType.MID}) for b in bars((99, 101, 103, 100, 99, 101, 103, 90)))
    # Raw indicator declarations are calculated only on each available prefix here.
    from quantlab.features import compute_features, validate_strategy_features
    features = compute_features(series, validate_strategy_features(spec), instrument=INSTRUMENT)
    reports = [run_backtest(spec, series, features, instrument=INSTRUMENT, config=research_config)]
    if walk:
        reports.append(run_walk_forward(spec, series, features, instrument=INSTRUMENT,
            config=research_config, walk_forward=WalkForwardConfig(train_size=2, test_size=2, step_size=2)))
    else:
        reports.append(run_holdout(spec, series, features, instrument=INSTRUMENT, config=research_config,
            split=HoldoutConfig(train=ValidationWindow(start=0, end=4), test=ValidationWindow(start=4, end=8))))
    reports.append(run_parameter_robustness((RobustnessCandidate(candidate_id="baseline", strategy=spec),),
        series, features, baseline_candidate_id="baseline", instrument=INSTRUMENT,
        config=research_config, window=ValidationWindow(start=0, end=8)))
    if report_changes:
        reports = report_changes(reports)
    datasets = (DatasetVersion(dataset_id="fixture", version_digest=stable_id("fixture-dataset-v1", series),
                               source_reference="synthetic:fixture"),)
    evidence = tuple(record(ResearchEvidence, report=r,
        result_digest=stable_id("paper-research-result-v1", r), request_digest=stable_id("fixture-request-v1", (spec, series, i)),
        datasets=datasets, provenance_reference=f"research:artifact-{i}", verifier="independent:verifier",
        verified_at=START+8*MINUTE, status="verified") for i, r in enumerate(reports))
    return evidence, datasets


def setup(spec=None, *, walk=False, policy_changes=None, maximum_events=5000, capital="1000", research_config=CONFIG):
    spec = strategy(no_exit=True) if spec is None else spec
    evidence, datasets = bundle(spec, walk=walk, research_config=research_config)
    pol = policy(**(policy_changes or {}))
    eligibility = record(EligibilityDecision, **binding(spec), policy_digest=pol.digest,
        evidence=tuple(EvidenceReference(evidence_id=e.record_id, result_digest=e.result_digest) for e in evidence),
        datasets=datasets, reviewer="human:eligibility", timestamp=START+9*MINUTE,
        eligible=True, reason_reference="human:recorded-review")
    account = PaperAccount(AccountConfig(account_id="account", denomination="USD",
        instrument=INSTRUMENT, starting_capital=D(capital), timestamp=START+10*MINUTE))
    cfg = StrategySessionConfig(**binding(spec), session_id="session", account=account.snapshot.config,
        timestamp=START+10*MINUTE, timeframe=Timeframe.M1, quantity=research_config.quantity,
        risk=research_config.risk, costs=research_config.execution_costs, maximum_events=maximum_events)
    return dict(strategy=spec, config=cfg, policy=pol, eligibility=eligibility,
        evidence=ResearchEvidenceStore(evidence), account_snapshot=account.snapshot), account, evidence


def runtime(**kwargs):
    args, account, evidence = setup(**kwargs)
    return StrategyRuntime(**args), account, args, evidence


def delivery(index=0, close=101, *, sequence=None, event_id=None, delay=timedelta(0), **changes):
    original = bars((close,) * (index + 1))[-1]
    b = original.model_copy(update={"price_type": PriceType.MID,
        "start_time": original.start_time+10*MINUTE, "end_time": original.end_time+10*MINUTE,
        "available_at": original.available_at+10*MINUTE})
    body = dict(event_id=event_id or f"bar-close-{index}", sequence=sequence or index+1,
        timestamp=b.end_time+delay, delivered_at=b.end_time+delay, bar=b)
    body.update(changes)
    return BarCloseDelivery(**body)


def close_quote(bar_delivery, *, sequence=1, bid="100", ask="102", **changes):
    t = bar_delivery.bar.end_time
    body = dict(event_id="actual-close-quote", sequence=sequence, timestamp=t, delivered_at=t,
        quote=MarketQuote(instrument_id=INSTRUMENT.instrument_id, source_id="synthetic",
            dataset_id="fixture", timestamp=t, available_at=t, bid=D(bid), ask=D(ask)))
    body.update(changes)
    return MarketDelivery(**body)


def opening(bar_delivery, *, sequence=3, price="101", **changes):
    t = bar_delivery.bar.end_time
    body = dict(event_id="actual-opening-quote", previous_close_id=bar_delivery.event_id,
        sequence=sequence, timestamp=t, delivered_at=t, bar_start=t, bar_end=t+MINUTE,
        timeframe=Timeframe.M1, opening_price=D(price), quote=MarketQuote(
            instrument_id=INSTRUMENT.instrument_id, source_id="synthetic", dataset_id="fixture",
            timestamp=t, available_at=t, bid=D(price), ask=D(price)))
    body.update(changes)
    return OpeningDelivery(**body)


def ready(**kwargs):
    rt, account, args, evidence = runtime(**kwargs)
    d = delivery(close=99 if rt.config.strategy_id == "short" else 101)
    rt.process(d)
    adapter = StrategyOrderAdapter(rt, account)
    return rt, adapter, account, d, args, evidence
