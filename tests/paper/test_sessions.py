"""Phase 18D negative, causal, replay and coordinated-publication regressions."""
from datetime import timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from quantlab.backtesting import BacktestConfig, ExecutionCostConfig, EvaluationResult
from quantlab.paper import (FillRecord, OrderState, PaperAccount, PaperIdentityConflict,
    PaperInputError, PaperSession, FeedProvenance, ReplayConfig, ReplayEvent,
    SessionCommand, SessionState as S, StaleFeedPolicy, StrategyRuntime, stable_id)
from quantlab.paper.feed import advance_clock
from quantlab.replay import HistoricalReplay, verify_replay
from quantlab.data.storage import DatasetMetadata, SQLiteDatasetStore
from quantlab.risk import RiskConfig
from tests.backtesting.helpers import START, MINUTE, INSTRUMENT, strategy
from .strategy_helpers import setup, delivery, close_quote, opening

T = START + 10 * MINUTE
SOURCE = FeedProvenance(dataset_id="fixture", version_digest=stable_id("fixture", "v1"),
    source_id="synthetic", source_reference="synthetic:declared-opening-feed",
    classification="synthetic-declared-openings")


def session(*, age=timedelta(0), maximum_inputs=100, **kwargs):
    args, _, _ = setup(**kwargs)
    cfg = ReplayConfig(strategy=args["config"], sources=(SOURCE,), maximum_inputs=maximum_inputs,
        stale=StaleFeedPolicy(policy_id="stale-v1", version=1, maximum_age=age))
    owners = {k: args[k] for k in ("strategy", "policy", "eligibility", "evidence")}
    return PaperSession(cfg, **owners), owners


def command(action, seq=1, t=T, identity=None):
    return SessionCommand(command_id=identity or f"{action}-{seq}", action=action,
        sequence=seq, timestamp=t, reason_reference=f"human:{action}")


def event(obs, provenance=SOURCE):
    if hasattr(obs, "bar"):
        market, occurrence, kind = obs.bar, obs.bar.end_time, "bar_close"
    else:
        market, occurrence = obs.quote, obs.quote.timestamp
        kind = "opening" if hasattr(obs, "bar_start") else "quote"
    return ReplayEvent(event_id=obs.event_id, kind=kind, sequence=obs.sequence,
        timestamp=obs.timestamp, delivered_at=obs.delivered_at, occurred_at=occurrence,
        available_at=market.available_at, provenance=provenance, observation=obs)


def control(kind, seq, t, **changes):
    body = dict(event_id=f"{kind}-{seq}", kind=kind, sequence=seq, timestamp=t,
        delivered_at=t, occurred_at=t, available_at=t, provenance=SOURCE)
    body.update(changes)
    return ReplayEvent(**body)


def inputs(price="101", close=101):
    bar = delivery(close=close, sequence=2)
    return (command("start"), event(bar), event(close_quote(bar, sequence=3)),
        event(opening(bar, sequence=5, price=price)))


def execute(**kwargs):
    owner, _ = session(**kwargs)
    items = inputs()
    owner.replay(items, maximum_events=len(items))
    return owner, items


@pytest.mark.parametrize("action,from_state", [("pause", S.CREATED), ("resume", S.CREATED),
    ("start", S.ACTIVE), ("resume", S.ACTIVE), ("start", S.PAUSED), ("pause", S.PAUSED)])
def test_invalid_lifecycle_leaves_state_unchanged(action, from_state):
    owner, _ = session()
    if from_state is not S.CREATED:
        owner.start(command("start"))
    if from_state is S.PAUSED:
        owner.pause(command("pause", 2))
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(command(action, 10))
    assert owner.snapshot == before


def test_lifecycle_exact_retry_conflicts_and_immutable_records():
    owner, _ = session()
    for i, action in enumerate(("start", "pause", "resume", "stop"), 1):
        item = command(action, i)
        outcome = getattr(owner, action)(item)
        before = owner.snapshot
        assert owner.process(item) is outcome
        assert owner.snapshot == before
        with pytest.raises(PaperIdentityConflict):
            owner.process(item.model_copy(update={"reason_reference": "human:different"}))
        with pytest.raises(ValidationError):
            outcome.reason = "changed"
    assert owner.snapshot.state is S.STOPPED
    assert owner.records[-1].transitions == (S.STOPPING, S.STOPPED)
    with pytest.raises(PaperInputError):
        owner.resume(command("resume", 5))
    assert owner.start(command("start")) is owner.records[0]


@pytest.mark.parametrize("action", ["stop", "fail"])
def test_termination_cancels_pending_atomically_without_exit(action):
    owner, _ = session()
    owner.replay(inputs()[:3], maximum_events=3)
    before = owner.snapshot
    assert before.account.reserved_funds > 0
    item = command(action, 6, T + MINUTE)
    result = owner.process(item)
    after = owner.snapshot
    assert after.state is (S.STOPPED if action == "stop" else S.FAILED)
    assert after.execution.kernel.state is OrderState.CANCELLED
    assert after.account.reservations == () and after.account.reserved_funds == 0
    assert after.account.position is None and after.account.fees_paid == 0
    assert result.financial[-1].kind == "release"
    assert result.orders[-1].cancelled
    assert owner.process(item) is result
    assert owner.snapshot == after


def test_stop_keeps_open_positions_non_liquidating():
    owner, _ = execute()
    financial = owner.snapshot.account
    owner.stop(command("stop", 6, T + MINUTE))
    assert owner.snapshot.account == financial
    assert financial.position is not None
    assert owner.records[-1].orders == ()


def test_paused_feed_never_evaluates_or_submits():
    owner, _ = session()
    owner.start(command("start"))
    owner.pause(command("pause", 2))
    bar = delivery(sequence=3)
    assert owner.process(event(bar)).reason == "inactive"
    assert owner.process(event(close_quote(bar, sequence=4))).reason == "inactive"
    assert owner.snapshot.runtime.decisions == ()
    assert owner.snapshot.account.state_version == 0
    owner.resume(command("resume", 5, bar.timestamp))
    next_bar = delivery(1, sequence=6)
    assert owner.process(event(next_bar)).decision.reason == "entry_signal"


def test_paused_pending_open_does_not_fill_and_stop_releases():
    owner, _ = session()
    owner.replay(inputs()[:3], maximum_events=3)
    owner.pause(command("pause", 4, T + MINUTE))
    before = owner.snapshot.account
    assert owner.process(inputs()[3]).reason == "inactive"
    assert owner.snapshot.account == before
    owner.stop(command("stop", 6, T + MINUTE))
    assert owner.snapshot.account.reservations == ()
    assert owner.snapshot.account.position is None


@pytest.mark.parametrize("sequence,t", [(1, T), (0, T), (2, T - MINUTE)])
def test_clock_backwards_rejected(sequence, t):
    owner, _ = session()
    owner.start(command("start"))
    before = owner.snapshot
    with pytest.raises((PaperInputError, ValidationError)):
        owner.process(command("pause", sequence, t))
    assert owner.snapshot == before


def test_equal_time_distinct_identity_order_and_duplicate_detection():
    owner, _ = session()
    owner.start(command("start"))
    a = control("heartbeat", 2, T)
    b = a.model_copy(update={"event_id": "distinct", "sequence": 3})
    first = owner.process(a)
    second = owner.process(b)
    assert owner.snapshot.feed.accepted == 2
    assert first.record_id != second.record_id
    assert owner.process(a) is first
    assert owner.snapshot.clock.sequence == 3
    with pytest.raises(PaperIdentityConflict):
        owner.process(a.model_copy(update={"timestamp": T + MINUTE}))
    with pytest.raises(PaperIdentityConflict):
        owner.process(a.model_copy(update={"sequence": -1}))


@pytest.mark.parametrize("field", ["occurred_at", "available_at", "delivered_at"])
def test_future_or_inconsistent_envelope_rejected(field):
    owner, _ = session()
    owner.start(command("start"))
    item = event(delivery(sequence=2))
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(item.model_copy(update={field: item.timestamp + MINUTE}))
    assert owner.snapshot == before


def test_delayed_bar_evaluation_retains_unavailable_no_retrospective_entry():
    owner, _ = session(age=MINUTE)
    owner.start(command("start"))
    out = owner.process(event(delivery(sequence=2, delay=MINUTE)))
    assert out.decision.reason == "late_bar"
    assert out.decision.long is EvaluationResult.UNAVAILABLE
    assert owner.snapshot.runtime.intent is None
    assert owner.snapshot.account.state_version == 0


def test_late_old_observation_never_rewinds_or_refreshes_feed():
    owner, _ = session(age=MINUTE)
    owner.start(command("start"))
    bar = delivery(1, sequence=2)
    owner.process(event(bar))
    old = close_quote(delivery(), sequence=3, event_id="old-arrival")
    old = old.model_copy(update={"timestamp": bar.timestamp, "delivered_at": bar.timestamp})
    # No intent on a FALSE bar; use another owner to avoid late close submission.
    owner, _ = session(age=MINUTE)
    owner.start(command("start"))
    bar = delivery(1, close=99, sequence=2)
    owner.process(event(bar))
    result = owner.process(event(old))
    assert result.reason == "observed"
    assert owner.snapshot.feed.last_market_at == bar.bar.end_time
    assert owner.snapshot.clock.timestamp == bar.timestamp


@pytest.mark.parametrize("extra,reason", [(timedelta(0), "evaluated"),
    (timedelta(microseconds=1), "stale")])
def test_stale_threshold_is_inclusive(extra, reason):
    owner, _ = session(age=MINUTE)
    owner.start(command("start"))
    result = owner.process(event(delivery(sequence=2, delay=MINUTE + extra)))
    assert result.reason == reason
    assert owner.snapshot.account.position is None


def test_missing_interruption_resumption_fresh_recovery_exhaustion():
    owner, _ = session()
    owner.start(command("start"))
    assert owner.process(control("heartbeat", 2, T)).reason == "missing"
    assert owner.process(control("interruption", 3, T)).reason == "interrupted"
    assert owner.process(event(delivery(sequence=4))).reason == "interrupted"
    assert owner.snapshot.runtime.decisions == ()
    assert owner.process(control("resumption", 5, T + MINUTE)).reason == "missing"
    fresh = delivery(1, sequence=6)
    assert owner.process(event(fresh)).reason == "evaluated"
    exhausted = control("exhaustion", 7, fresh.timestamp)
    assert owner.process(exhausted).reason == "exhausted"
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(control("heartbeat", 8, fresh.timestamp))
    assert owner.snapshot == before
    assert owner.process(exhausted) is owner.records[-1]
    owner.stop(command("stop", 8, fresh.timestamp))


@pytest.mark.parametrize("kind", ["resumption", "interruption"])
def test_invalid_feed_transition_is_atomic(kind):
    owner, _ = session()
    owner.start(command("start"))
    if kind == "interruption":
        owner.process(control("interruption", 2, T))
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(control(kind, 3, T))
    assert owner.snapshot == before


def test_stale_or_interrupted_pending_open_cannot_create_exposure():
    owner, _ = session()
    owner.replay(inputs()[:3], maximum_events=3)
    before = owner.snapshot.account
    owner.process(control("interruption", 4, T + MINUTE))
    out = owner.process(inputs()[3])
    assert out.reason == "interrupted" and out.orders == ()
    assert owner.snapshot.account == before
    owner.stop(command("stop", 6, T + MINUTE))
    assert owner.snapshot.account.reservations == ()


def test_heartbeat_logical_staleness_and_recovery():
    owner, _ = session()
    owner.start(command("start"))
    owner.process(event(delivery(sequence=2, close=99)))
    assert owner.process(control("heartbeat", 3, T + MINUTE + timedelta(microseconds=1))).reason == "stale"
    fresh = delivery(1, sequence=4)
    assert owner.process(event(fresh)).reason == "evaluated"
    assert owner.snapshot.feed.reason == "fresh"


def test_replay_reproducibility_independence_and_prefix_snapshot():
    left, args = session()
    right, _ = session()
    prefix = inputs()[:3]
    left.replay(prefix, maximum_events=3)
    saved = left.snapshot
    saved_wire = saved.canonical_json()
    right.replay(prefix, maximum_events=3)
    assert left.snapshot == right.snapshot
    left.process(inputs()[3])
    assert right.snapshot == saved
    assert saved.canonical_json() == saved_wire
    assert left.records[:3] == saved.records
    right.process(inputs()[3])
    assert left.snapshot == right.snapshot
    verified = verify_replay(left.config, inputs(), **args)
    assert verified.verified and verified.input_count == 4
    assert verified == verify_replay(left.config, inputs(), **args)


def test_bounded_replay_consumes_no_future_suffix():
    owner, _ = session()
    def producer():
        yield command("start")
        yield event(delivery(sequence=2, close=99))
        raise AssertionError("future suffix consumed")
    assert len(owner.replay(producer(), maximum_events=2)) == 2
    assert len(owner.records) == 2
    assert owner.snapshot.runtime.decisions[-1].reason == "no_signal"


@pytest.mark.parametrize("bound", [-1, 101, True, 1.5])
def test_invalid_processing_bound_rejected(bound):
    owner, _ = session()
    with pytest.raises(PaperInputError):
        owner.replay((), maximum_events=bound)


def test_missing_opening_keeps_reservation_until_explicit_stop():
    owner, _ = session()
    owner.replay(inputs()[:3], maximum_events=3)
    out = owner.process(control("exhaustion", 5, T + MINUTE))
    assert out.account.position is None and out.account.reservations
    assert not any(isinstance(r, FillRecord) for row in owner.records for r in row.orders)
    owner.stop(command("stop", 6, T + MINUTE))
    assert owner.snapshot.account.reservations == ()


@pytest.mark.parametrize("change", ["spread", "late", "adjacency", "timeframe", "historical"])
def test_unproven_or_nonadjacent_opening_cannot_fill(change):
    from quantlab.data import Timeframe
    owner, _ = session()
    owner.replay(inputs()[:3], maximum_events=3)
    op = inputs()[3].observation
    before = owner.snapshot
    if change == "spread":
        op = op.model_copy(update={"quote": op.quote.model_copy(update={"ask": D("102")})})
    elif change == "late":
        op = op.model_copy(update={"delivered_at": op.timestamp + MINUTE})
    elif change == "adjacency":
        op = op.model_copy(update={"previous_close_id": "different-close"})
    elif change == "timeframe":
        op = op.model_copy(update={"timeframe": Timeframe.M5})
    else:
        op = None
    with pytest.raises((ValidationError, PaperInputError)):
        bad = event(op) if op is not None else event(inputs()[3].observation,
            SOURCE.model_copy(update={"classification": "historical"}))
        owner.process(bad)
    assert owner.snapshot == before


def test_after_later_bar_open_cannot_execute_retrospectively():
    owner, _ = session()
    owner.replay(inputs()[:3], maximum_events=3)
    owner.process(event(delivery(1, sequence=5)))
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(event(opening(delivery(sequence=2), sequence=6)))
    assert owner.snapshot == before


@pytest.mark.parametrize("side", ["long", "short"])
def test_account_owned_fill_audit_fee_and_exact_retry(side):
    from quantlab.strategies import Direction
    spec = strategy(no_exit=True, direction=Direction.SHORT if side == "short" else Direction.LONG)
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(slippage=D("0.5"),
            commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1")))
    owner, _ = session(spec=spec, research_config=config)
    items = inputs(close=99 if side == "short" else 101)
    owner.replay(items, maximum_events=4)
    snap = owner.snapshot
    outcome = owner.records[-1]
    assert snap.account.fees_paid == D("1.2")
    assert snap.account.position.quantity == 2
    assert snap.account.reservations == ()
    assert outcome.financial[-1].kind == "settle"
    fill = next(r for r in outcome.orders if isinstance(r, FillRecord))
    assert fill.source.quote == items[-1].observation.quote
    assert snap.execution.openings == (items[-1].observation,)
    assert snap.runtime.intent.decision_id == owner.records[1].decision.record_id
    for item, result in zip(items, owner.records):
        assert owner.process(item) is result
    assert owner.snapshot == snap


def test_unfunded_open_cancels_releases_and_records_no_phantom_fill():
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"))
    owner, _ = session(capital="300", research_config=config)
    owner.replay(inputs(price="200"), maximum_events=4)
    snap = owner.snapshot
    assert snap.execution.kernel.state is OrderState.CANCELLED
    assert snap.account.fees_paid == 0 and snap.account.position is None
    assert snap.account.reservations == ()
    assert owner.records[-1].financial[-1].kind == "release"
    assert owner.records[-1].orders[-1].reason == "account_unfunded"


def test_risk_rejection_uses_existing_engine():
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        risk=RiskConfig(max_position_quantity=D("1")))
    owner, _ = session(research_config=config)
    owner.replay(inputs()[:3], maximum_events=3)
    assert owner.snapshot.execution.kernel.state is OrderState.REJECTED
    assert owner.snapshot.account.state_version == 0
    assert owner.records[-1].orders[-1].reason == "risk_rejected"


@pytest.mark.parametrize("failure,step",
    [(failure, step) for failure in ("prepare-record", "stage-record", "before-swap", "after-swap")
        for step in (1, 2, 3, 4)] + [("runtime-index", 1)]
    + [(failure, step) for failure in ("financial-index", "account-publication") for step in (2, 3)])
def test_failure_injection_keeps_all_components_and_retry_indexes_unchanged(monkeypatch, failure, step):
    owner, _ = session()
    items = (*inputs(), command("stop", 6, T + MINUTE))
    owner.replay(items[:step], maximum_events=step)
    before = owner.snapshot
    root = owner._publication
    seen = dict(owner._seen)
    inputs_before = tuple(root.runtime._inputs)
    features_before = dict(root.runtime._feature_index)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            assert owner.snapshot == before
            raise MemoryError("injected preparation failure")
        if failure == "prepare-record":
            patch.setattr(owner, "_prepare_record", fail)
        elif failure == "stage-record":
            original = owner._stage_record
            def stage(*args):
                original(*args)
                fail()
            patch.setattr(owner, "_stage_record", stage)
        elif failure == "runtime-index":
            original = StrategyRuntime._stage_indexes
            def stage(runtime, *args):
                original(runtime, *args)
                fail()
            patch.setattr(StrategyRuntime, "_stage_indexes", stage)
        elif failure == "financial-index":
            original = PaperAccount._stage_indexes
            def stage(account, *args):
                original(account, *args)
                fail()
            patch.setattr(PaperAccount, "_stage_indexes", stage)
        elif failure == "account-publication":
            original = PaperAccount.__setattr__
            def setter(account, name, value):
                original(account, name, value)
                if name == "_publication":
                    fail()
            patch.setattr(PaperAccount, "__setattr__", setter)
        else:
            original = PaperSession.__setattr__
            def setter(session, name, value):
                if session is owner and name == "_publication" and value is not root:
                    if failure == "after-swap":
                        original(session, name, value)
                    raise MemoryError("injected publication failure")
                original(session, name, value)
            patch.setattr(PaperSession, "__setattr__", setter)
        with pytest.raises(MemoryError):
            owner.process(items[step])
    assert owner.snapshot == before and owner._publication is root
    assert owner._seen == seen and not owner._busy
    assert tuple(root.runtime._inputs) == inputs_before
    assert root.runtime._feature_index == features_before
    outcome = owner.process(items[step])
    after = owner.snapshot
    assert owner.process(items[step]) is outcome
    assert owner.snapshot == after
    with pytest.raises(PaperIdentityConflict):
        owner.process(items[step].model_copy(update={"timestamp": items[step].timestamp + MINUTE}))
    assert owner.snapshot == after


def test_processing_limit_exact_retry_and_identity_collision():
    owner, _ = session(maximum_inputs=2)
    owner.start(command("start"))
    heart = control("heartbeat", 2, T)
    result = owner.process(heart)
    with pytest.raises(PaperInputError):
        owner.process(control("heartbeat", 3, T))
    assert owner.process(heart) is result
    with pytest.raises(PaperIdentityConflict):
        owner.process(command("pause", 3, identity=heart.event_id))


@pytest.mark.parametrize("change", ["unapproved", "digest", "exit", "quantity"])
def test_session_reuses_admission_restrictions(change):
    args, _, _ = setup()
    if change == "unapproved":
        from quantlab.strategies import ApprovalState
        args["strategy"] = args["strategy"].model_copy(update={"state": ApprovalState.DRAFT, "approval": None})
    elif change == "digest":
        args["config"] = args["config"].model_copy(update={"strategy_digest": "0" * 64})
    elif change == "exit":
        args, _, _ = setup(spec=strategy())
    else:
        args["config"] = args["config"].model_copy(update={"quantity": D("1.5")})
    with pytest.raises((PaperInputError, ValidationError)):
        cfg = ReplayConfig(strategy=args["config"], sources=(SOURCE,),
            stale=StaleFeedPolicy(policy_id="stale", version=1, maximum_age=MINUTE))
        PaperSession(cfg, **{k: args[k] for k in ("strategy", "policy", "eligibility", "evidence")})


def test_negative_stale_policy():
    with pytest.raises(ValidationError):
        StaleFeedPolicy(policy_id="invalid", version=1, maximum_age=-MINUTE)


@pytest.mark.parametrize("observation_kind", ["bar", "quote"])
def test_historical_store_replay_preserves_provenance_never_invents_openings(tmp_path, observation_kind):
    bar = delivery(sequence=2, close=99)
    observations = (bar.bar,) if observation_kind == "bar" else (close_quote(bar).quote,)
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    identity = store.save(observations, DatasetMetadata(instrument=INSTRUMENT,
        description="offline fixture", licensing="synthetic", transformation="none"))
    adapter = HistoricalReplay(store.load(identity))
    events = tuple(adapter.events(first_sequence=2, maximum_events=10))
    assert len(events) == 1
    assert events[0].kind == ("bar_close" if observation_kind == "bar" else "quote")
    assert events[0].provenance.version_digest == identity.removeprefix("sha256:")
    assert events[0].provenance.dataset_id == observations[0].dataset_id
    assert events[0].observation.bar == bar.bar if observation_kind == "bar" else events[0].observation.quote == observations[0]
    assert events == tuple(adapter.events(first_sequence=2, maximum_events=10))
    owner, args = session()
    cfg = owner.config.model_copy(update={"sources": adapter.sources})
    owner = PaperSession(cfg, **args)
    owner.start(command("start"))
    owner.replay(events, maximum_events=10)
    assert owner.snapshot.account.position is None
    assert owner.snapshot.execution.openings == ()


def test_architecture_no_io_wallclock_background_or_new_authority(monkeypatch):
    import asyncio
    import builtins
    import socket
    import subprocess
    import threading
    import time
    import urllib.request
    owner, args = session()
    items = inputs()
    def denied(*args, **kwargs):
        raise AssertionError("external effect forbidden")
    with monkeypatch.context() as patch:
        for obj, name in [(builtins, "open"), (socket, "socket"), (socket, "create_connection"),
            (subprocess, "Popen"), (threading.Thread, "start"), (asyncio, "create_task"),
            (time, "time"), (time, "sleep"), (urllib.request, "urlopen")]:
            patch.setattr(obj, name, denied)
        owner.replay(items, maximum_events=4)
        assert verify_replay(owner.config, items, **args).verified
    assert owner.snapshot.account.position is not None


@pytest.mark.parametrize("failure", ["record-serialization", "root-allocation", "financial-staging"])
@pytest.mark.parametrize("terminal", ["fill", "unfunded", "pending-stop"])
def test_terminal_candidate_failures_preserve_financial_and_provenance_state(monkeypatch, failure, terminal):
    from quantlab.paper import SessionRecord
    import quantlab.paper.sessions as module
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        execution_costs=ExecutionCostConfig(fixed_fee_per_fill=D("1")))
    owner, _ = session(capital="300" if terminal == "unfunded" else "1000", research_config=config)
    items = inputs(price="200" if terminal == "unfunded" else "101")
    owner.replay(items[:3], maximum_events=3)
    item = command("stop", 5, T + MINUTE) if terminal == "pending-stop" else items[-1]
    before = owner.snapshot
    with monkeypatch.context() as patch:
        if failure == "record-serialization":
            original = SessionRecord.canonical_json
            def fail(record):
                original(record)
                assert owner.snapshot == before
                raise ValueError("audit serialization failed")
            patch.setattr(SessionRecord, "canonical_json", fail)
        elif failure == "root-allocation":
            def fail(*args, **kwargs):
                assert owner.snapshot == before
                raise MemoryError("candidate root allocation failed")
            patch.setattr(module, "replace", fail)
        else:
            original = PaperAccount._prepare_apply
            def fail(account, item):
                original(account, item)
                assert owner.snapshot == before
                raise MemoryError("financial preparation failed")
            patch.setattr(PaperAccount, "_prepare_apply", fail)
        with pytest.raises((ValueError, MemoryError)):
            owner.process(item)
    assert owner.snapshot == before
    assert owner.snapshot.execution.openings == ()
    assert owner.snapshot.account.fees_paid == 0
    assert owner.process(item).financial
    assert owner.snapshot.account.reservations == ()
    assert owner.snapshot.account.fees_paid == (D("1") if terminal == "fill" else D("0"))


def test_raw_features_unavailable_and_failed_suffix_index_cleanup(monkeypatch):
    from quantlab.strategies import FeatureOperand, FeatureReference, FeatureType
    from tests.backtesting.helpers import group, rule
    feature = FeatureReference(feature_id="raw-close", implementation_id="close", feature_type=FeatureType.INDICATOR)
    spec = strategy(no_exit=True, features=(feature,),
        entry=group(rule(left=FeatureOperand(feature_id=feature.feature_id, offset=1))))
    owner, _ = session(spec=spec)
    owner.start(command("start"))
    first = owner.process(event(delivery(sequence=2)))
    assert first.decision.long is EvaluationResult.UNAVAILABLE
    before = owner.snapshot
    features = dict(owner._publication.runtime._feature_index)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            assert owner.snapshot == before
            raise MemoryError("post-evaluation record failure")
        patch.setattr(owner, "_prepare_record", fail)
        with pytest.raises(MemoryError):
            owner.process(event(delivery(1, sequence=3)))
    assert owner.snapshot == before
    assert owner._publication.runtime._feature_index == features
    decision = owner.process(event(delivery(1, sequence=3))).decision
    assert decision.long is EvaluationResult.TRUE
    assert decision.dependencies == ("bar-close-0", "bar-close-1")
    assert decision.features[0].timestamp <= decision.timestamp


def test_event_series_reordering_and_delivery_reordering_are_transactional():
    owner, _ = session(age=2 * MINUTE)
    owner.start(command("start"))
    owner.process(event(delivery(1, sequence=2, close=99)))
    before = owner.snapshot
    old = delivery(sequence=3, close=99, delay=MINUTE)
    with pytest.raises(PaperInputError):
        owner.process(event(old))
    assert owner.snapshot == before
    back_delivery = control("heartbeat", 3, T + 2 * MINUTE,
        delivered_at=T + MINUTE, available_at=T + MINUTE, occurred_at=T + MINUTE)
    with pytest.raises(PaperInputError):
        owner.process(back_delivery)
    assert owner.snapshot == before


def test_snapshot_roundtrips_and_event_path_never_serializes_full_history(monkeypatch):
    from quantlab.paper import SessionSnapshot, RuntimeSnapshot
    owner, _ = session(maximum_inputs=300, maximum_events=250)
    owner.start(command("start"))
    def deny(*args, **kwargs):
        raise AssertionError("full history serialized during event processing")
    with monkeypatch.context() as patch:
        patch.setattr(SessionSnapshot, "canonical_json", deny)
        patch.setattr(RuntimeSnapshot, "canonical_json", deny)
        for i in range(250):
            owner.process(event(delivery(i, close=99, sequence=i + 2)))
    snapshot = owner.snapshot
    assert len(snapshot.records) == 251 and len(snapshot.runtime.decisions) == 250
    assert SessionSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert len(owner._publication.runtime._publication.history) == 2


def test_foreign_provenance_identity_and_reentrant_mutation_rejected(monkeypatch):
    owner, _ = session()
    owner.start(command("start"))
    item = control("heartbeat", 2, T)
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(item.model_copy(update={"provenance": SOURCE.model_copy(update={"version_digest": "0" * 64})}))
    assert owner.snapshot == before
    original = owner._prepare_record
    def inspect(**values):
        with pytest.raises(PaperInputError, match="already in progress"):
            owner.process(item)
        assert owner.snapshot == before
        return original(**values)
    monkeypatch.setattr(owner, "_prepare_record", inspect)
    owner.process(item)
    assert not owner._busy



def test_equal_payload_distinct_market_identities_are_retained_without_fills():
    owner, _ = session()
    owner.start(command("start"))
    quote = close_quote(delivery(), sequence=2, event_id="quote-a")
    first = owner.process(event(quote))
    second = owner.process(event(quote.model_copy(update={"sequence": 3, "event_id": "quote-b"})))
    assert first.market_event.observation.quote == second.market_event.observation.quote
    assert first.record_id != second.record_id
    assert owner.snapshot.feed.accepted == 2
    assert owner.snapshot.account.state_version == 0


def test_stale_close_cannot_submit_reserved_exposure():
    owner, _ = session()
    owner.replay(inputs()[:2], maximum_events=2)
    before = owner.snapshot.account
    obs = close_quote(delivery(), sequence=3)
    obs = obs.model_copy(update={"timestamp": obs.timestamp + MINUTE,
        "delivered_at": obs.timestamp + MINUTE})
    result = owner.process(event(obs))
    assert result.reason == "stale" and result.orders == ()
    assert owner.snapshot.account == before
    assert owner.snapshot.execution.kernel.submission is None


def test_execution_risk_rejection_cancels_releases_and_acknowledges():
    config = BacktestConfig(initial_capital=D("1000"), quantity=D("2"),
        risk=RiskConfig(max_notional_exposure=D("300")))
    owner, _ = session(research_config=config)
    items = inputs(price="200")
    owner.replay(items, maximum_events=4)
    snap = owner.snapshot
    assert snap.execution.kernel.state is OrderState.CANCELLED
    assert snap.account.position is None and snap.account.fees_paid == 0
    assert snap.account.reservations == ()
    assert snap.execution.openings == (items[-1].observation,)
    assert owner.records[-1].orders[-1].reason == "risk_rejected"
    assert owner.records[-1].financial[-1].kind == "release"


def test_decimal_context_cannot_change_replay_financial_outcomes():
    from decimal import Context, Inexact, Rounded, ROUND_DOWN, localcontext
    left, _ = execute()
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = context.traps[Rounded] = True
        right, _ = execute()
        assert left.snapshot == right.snapshot
        assert context.prec == 2 and context.traps[Inexact] and context.traps[Rounded]


def test_historical_dataset_integrity_is_checked_without_rewriting_provenance(tmp_path):
    from dataclasses import replace
    bar = delivery().bar
    store = SQLiteDatasetStore(tmp_path / "dataset.sqlite")
    identity = store.save((bar,), DatasetMetadata(instrument=INSTRUMENT,
        description="synthetic", licensing="fixture", transformation="none"))
    stored = store.load(identity)
    corrupted = replace(stored, observations=(bar.model_copy(update={"close": D("100")}),))
    with pytest.raises((ValueError, PaperInputError)):
        HistoricalReplay(corrupted)


@pytest.mark.parametrize("action", ["stop", "fail"])
def test_capacity_reserves_one_terminal_record_to_release_pending_order(action):
    owner, args = session(maximum_inputs=3)
    prefix = inputs()[:3]
    owner.replay(prefix, maximum_events=3)
    assert len(owner.records) == 3 and owner.snapshot.account.reservations
    before = owner.snapshot
    with pytest.raises(PaperInputError, match="capacity"):
        owner.process(control("heartbeat", 5, T + MINUTE))
    assert owner.snapshot == before
    terminal = command(action, 5, T + MINUTE)
    result = owner.process(terminal)
    assert len(owner.records) == 4
    assert owner.snapshot.account.reservations == ()
    assert owner.snapshot.execution.kernel.state is OrderState.CANCELLED
    assert result.command == terminal and result.market_event is None
    assert owner.process(terminal) is result
    assert verify_replay(owner.config, (*prefix, terminal), **args).verified
    with pytest.raises(PaperInputError):
        owner.process(command("stop", 6, T + MINUTE))
    assert len(owner.records) == 4
