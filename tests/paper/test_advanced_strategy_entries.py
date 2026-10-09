"""Causal, separately approved entries reuse the owned advanced engine."""
from decimal import Decimal as D
import pytest
from pydantic import ValidationError
from quantlab.paper import (PaperSession, PaperAccount, PaperInputError, PaperIdentityConflict,
    OrderState as S, AdvancedEntryIntent, AdvancedEntryPolicy, AdvancedEligibilityDecision,
    AdmissionError, SessionSnapshot, AdvancedFillRecord, stable_id)
from quantlab.paper.strategy_models import record
from quantlab.strategies import ApprovalState
from quantlab.risk import RiskConfig
from tests.paper.advanced_entry_helpers import session, configuration, prefix, quote, cancel, T
from tests.paper.test_sessions import event, command
from tests.paper.strategy_helpers import setup, delivery, opening
from tests.backtesting.helpers import MINUTE


@pytest.mark.parametrize("kind,prices", [("market", ["101","102"]),
    ("limit", ["101","100","99"]), ("stop_market", ["104","104","105"]),
    ("stop_limit", ["105","105","104","103"])])
def test_approved_types_partial_final_and_attribution(kind, prices):
    owner, _ = session(kind=kind)
    items = prefix(price=prices[0])
    for item in items:
        owner.process(item)
    intent = owner.snapshot.runtime.intent
    assert type(intent) is AdvancedEntryIntent
    assert intent.command_id == owner.snapshot.execution.kernel.submission.command_id
    assert intent.execution_approval_id == owner.config.strategy.execution_approval.record_id
    assert intent.admission_id == owner.snapshot.runtime.admission.record_id
    assert owner.records[2].entry_intent == intent
    assert owner.records[2].entry_order.activation is not None
    for n, price in enumerate(prices[1:], 6):
        item = quote(n, price)
        out = owner.process(item)
        saved = owner.snapshot
        assert owner.process(item) is out and owner.snapshot == saved
    snapshot = owner.snapshot
    assert snapshot.execution.kernel.state is S.FILLED
    assert snapshot.account.position.quantity == 2
    assert snapshot.account.fees_paid == D("2.2") and not snapshot.account.reservations
    fills = [r for row in owner.records for r in row.orders if isinstance(r, AdvancedFillRecord)]
    assert len(fills) == 2 and sum(f.execution.quantity for f in fills) == 2
    assert all(f.submission.command_id == intent.command_id for f in fills)
    assert snapshot.execution.openings == (items[-1].observation,)
    assert SessionSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


@pytest.mark.parametrize("change", ["draft", "digest", "approval", "policy", "eligibility", "configuration", "revision"])
def test_unauthorized_admission_cannot_create_order(change):
    cfg, owners = configuration()
    if change == "draft":
        owners["strategy"] = owners["strategy"].model_copy(update={"state": ApprovalState.DRAFT, "approval": None})
    elif change == "digest":
        owners["strategy"] = owners["strategy"].model_copy(update={"content":
            owners["strategy"].content.model_copy(update={"name": "altered"})})
    elif change == "approval":
        cfg = cfg.model_copy(update={"strategy": cfg.strategy.model_copy(update={"execution_approval":
            cfg.strategy.execution_approval.model_copy(update={"configuration_digest": "0"*64})})})
    elif change in ("policy", "eligibility"):
        old, _, _ = setup()
        owners[change] = old[change]
    elif change == "configuration":
        cfg = cfg.model_copy(update={"strategy": cfg.strategy.model_copy(update={"quantity": D("3")})})
    else:
        owners["strategy"] = owners["strategy"].revise(owners["strategy"].content)
    with pytest.raises((PaperInputError, ValidationError)):
        PaperSession(cfg, **owners)


@pytest.mark.parametrize("change", ["configuration", "approval", "timestamp", "false"])
def test_execution_eligibility_binds_configuration_and_approval(change):
    cfg, owners = configuration()
    body = owners["eligibility"].model_dump(exclude={"record_id"})
    body.update({"configuration_digest": "0"*64} if change == "configuration" else
        {"execution_approval_id": "0"*64} if change == "approval" else
        {"timestamp": T-2*MINUTE} if change == "timestamp" else {"eligible": False})
    owners["eligibility"] = record(AdvancedEligibilityDecision, **body)
    with pytest.raises(AdmissionError, match="eligibility_mismatch"):
        PaperSession(cfg, **owners)


@pytest.mark.parametrize("parameters", [dict(order_type="stop_market", stop_price=D("103"), time_in_force="ioc"),
    dict(order_type="stop_limit", stop_price=D("103")), dict(order_type="market", limit_price=D("100")),
    dict(order_type="limit"), dict(order_type="market", time_in_force="day"),
    dict(order_type="market", activation_policy="same-event"), dict(order_type="market", maximum_inputs=1000)])
def test_unsupported_entry_contracts_fail_explicitly(parameters):
    with pytest.raises(ValidationError):
        AdvancedEntryPolicy(policy_id="invalid", version=1, **parameters)


def test_legacy_timing_approval_and_content_remain_byte_identical():
    from tests.paper.test_sessions import session as legacy_session, inputs
    owner, _ = legacy_session()
    before = owner.config.canonical_json()
    for item in inputs()[:3]:
        owner.process(item)
    owner.process(quote(5, "100", time=T+MINUTE))
    assert owner.snapshot.account.position is None
    owner.process(event(opening(delivery(sequence=2), sequence=6)))
    assert owner.snapshot.execution.kernel.state is S.FILLED
    assert owner.config.canonical_json() == before
    assert owner.snapshot.runtime.intent.execution_policy == "explicit-locked-next-open-v1"
    assert "entry_order" not in owner.records[-1].model_dump_json()


@pytest.mark.parametrize("kind", ["market", "limit", "stop_market", "stop_limit"])
def test_missing_opening_gate_blocks_same_timestamp_quote(kind):
    owner, _ = session(kind=kind)
    for item in prefix()[:3]:
        owner.process(item)
    before = owner.snapshot
    with pytest.raises(PaperInputError, match="opening provenance"):
        owner.process(quote(5,"104", time=T+MINUTE))
    assert owner.snapshot == before and before.account.fees_paid == 0


def test_equal_timestamp_trigger_then_later_delivery_fill():
    owner, _ = session(kind="stop_market")
    for item in prefix(price="104"):
        owner.process(item)
    snap = owner.snapshot
    assert owner.records[-1].entry_order.trigger is not None and snap.account.position is None
    owner.process(quote(6,"104",time=T+MINUTE))
    assert owner.snapshot.account.position.quantity == 1
    owner.process(quote(7,"104",time=T+MINUTE))
    assert owner.snapshot.account.position.quantity == 1
    owner.process(quote(8,"105",time=T+MINUTE))
    assert owner.snapshot.execution.kernel.state is S.FILLED


@pytest.mark.parametrize("change", ["adjacency", "historical", "timeframe", "late", "spread"])
def test_untrusted_opening_cannot_activate_matching(change):
    from quantlab.data import Timeframe
    owner, _ = session()
    for item in prefix()[:3]:
        owner.process(item)
    before = owner.snapshot
    op = prefix()[-1].observation
    source = before.config.sources[0]
    if change == "adjacency":
        op = op.model_copy(update={"previous_close_id": "unrelated"})
    elif change == "timeframe":
        op = op.model_copy(update={"timeframe": Timeframe.M5})
    elif change == "late":
        op = op.model_copy(update={"delivered_at": op.timestamp+MINUTE})
    elif change == "spread":
        op = op.model_copy(update={"quote": op.quote.model_copy(update={"ask": D("102")})})
    else:
        source = source.model_copy(update={"classification": "historical"})
    with pytest.raises((ValidationError, PaperInputError)):
        owner.process(event(op, source))
    assert owner.snapshot == before


def test_gate_survives_later_decisions_but_missed_opening_is_not_retroactive():
    owner, _ = session()
    for item in prefix():
        owner.process(item)
    assert owner.process(event(delivery(1, sequence=6))).decision.reason == "single_entry_consumed"
    owner.process(quote(7,"100")); owner.process(quote(8,"99"))
    assert owner.snapshot.execution.kernel.state is S.FILLED
    missed, _ = session()
    for item in prefix()[:3]:
        missed.process(item)
    missed.process(event(delivery(1, sequence=5)))
    before = missed.snapshot
    with pytest.raises(PaperInputError):
        missed.process(event(opening(delivery(sequence=2), sequence=6)))
    assert missed.snapshot == before


@pytest.mark.parametrize("kind", ["market", "limit"])
def test_ioc_partial_expires_and_releases_remaining_reservation(kind):
    owner, _ = session(kind=kind, tif="ioc")
    for item in prefix(price="100"):
        owner.process(item)
    snap = owner.snapshot
    assert snap.execution.kernel.state is S.EXPIRED
    assert snap.account.position.quantity == 1 and snap.account.fees_paid == D("1.1")
    assert not snap.account.reservations


def test_stale_and_paused_inputs_cannot_fill_or_trigger():
    owner, _ = session(kind="stop_market")
    for item in prefix():
        owner.process(item)
    assert owner.process(quote(6,"104",time=T+12*MINUTE,age=11*MINUTE)).reason == "stale"
    assert owner.records[-1].entry_order.trigger is None
    owner.process(command("pause",7,T+12*MINUTE))
    assert owner.process(quote(8,"104",time=T+12*MINUTE)).reason == "inactive"
    assert owner.records[-1].entry_order.trigger is None
    owner.process(command("resume",9,T+12*MINUTE))
    owner.process(quote(10,"104",time=T+12*MINUTE))
    assert owner.records[-1].entry_order.trigger is not None


@pytest.mark.parametrize("stage", ["submission", "execution"])
def test_risk_rejection_does_not_fill_or_charge(stage):
    owner, _ = session(kind="market", risk=RiskConfig(max_notional_exposure=D("200") if stage == "submission" else D("300")))
    for item in prefix(price="200")[:3 if stage == "submission" else 4]:
        owner.process(item)
    snap = owner.snapshot
    assert snap.execution.kernel.state is (S.REJECTED if stage == "submission" else S.CANCELLED)
    assert snap.account.position is None and snap.account.fees_paid == 0 and not snap.account.reservations


def test_funding_rejection_is_atomic_and_partial_gap_has_no_phantom_fill():
    owner, _ = session(kind="market", capital="100")
    for item in prefix()[:2]:
        owner.process(item)
    before = owner.snapshot
    with pytest.raises(PaperInputError):
        owner.process(prefix()[2])
    assert owner.snapshot == before and before.execution.kernel.submission is None
    owner, _ = session(kind="market", capital="210")
    for item in prefix():
        owner.process(item)
    assert owner.snapshot.account.position.quantity == 1
    out = owner.process(quote(6,"200"))
    assert not any(isinstance(r, AdvancedFillRecord) for r in out.orders)
    snap = owner.snapshot
    assert snap.execution.kernel.state is S.CANCELLED and snap.account.position.quantity == 1
    assert snap.account.fees_paid == D("1.1") and not snap.account.reservations


@pytest.mark.parametrize("partial", [False, True])
def test_cancel_race_acknowledgement_and_nonliquidating_stop(partial):
    owner, _ = session(kind="market" if partial else "limit")
    for item in prefix():
        owner.process(item)
    owner.process(cancel(owner,6))
    assert owner.snapshot.execution.kernel.pending_cancellation is not None
    owner.process(quote(7,"100"))
    quantity = owner.snapshot.account.position.quantity
    ack = cancel(owner,8,"ack_cancel_entry")
    receipt = owner.process(ack)
    assert owner.process(ack) is receipt and not owner.snapshot.account.reservations
    owner.stop(command("stop",9,T+5*MINUTE))
    assert owner.snapshot.account.position.quantity == quantity


@pytest.mark.parametrize("failure", ["financial", "record", "publication"])
@pytest.mark.parametrize("operation", ["submit", "open", "partial", "final", "cancel", "ack"])
def test_session_failures_rollback_only_candidate_suffix(monkeypatch, failure, operation):
    owner, _ = session()
    items = list(prefix())+[quote(6,"100"),quote(7,"99")]
    count = {"submit":2, "open":3, "partial":4, "final":5, "cancel":5, "ack":5}[operation]
    for item in items[:count]:
        owner.process(item)
    if operation == "ack":
        owner.process(cancel(owner,7))
    target = {"submit":items[2], "open":items[3], "partial":items[4], "final":items[5],
        "cancel":cancel(owner,7), "ack":cancel(owner,8,"ack_cancel_entry")}[operation]
    before = owner.snapshot
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise MemoryError("injected")
        if failure == "financial":
            patch.setattr(PaperAccount, "_publish", fail)
        else:
            patch.setattr(owner, "_prepare_record" if failure == "record" else "_publish_candidate", fail)
        with pytest.raises(MemoryError):
            owner.process(target)
    assert owner.snapshot == before
    out = owner.process(target)
    after = owner.snapshot
    assert owner.process(target) is out and owner.snapshot == after


@pytest.mark.parametrize("length", [0, 8, 64, 200])
@pytest.mark.parametrize("operation", ["resting", "trigger", "partial", "final", "cancel"])
def test_advanced_entry_processing_never_copies_retained_history(monkeypatch, length, operation):
    from tests.paper.test_history_hot_path import protect_prefix
    owner, _ = session(kind="stop_market" if operation == "trigger" else "limit", maximum_inputs=256)
    for item in prefix():
        owner.process(item)
    for n in range(6,6+length):
        owner.process(quote(n,"101"))
    n = 6+length
    if operation == "final":
        owner.process(quote(n,"100")); n += 1
    target = cancel(owner,n) if operation == "cancel" else quote(n,
        "104" if operation == "trigger" else "101" if operation == "resting" else "99")
    with monkeypatch.context() as patch:
        copies = protect_prefix(patch)
        out = owner.process(target)
        assert owner.process(target) is out
    assert max(copies) <= 16 and len(copies) <= 64*16
    assert len(owner._publication.account._publication.entry_gates) == 1


def test_no_record_agent_or_mcp_authority_bypass():
    owner, _ = session()
    before = owner.snapshot
    for value in (owner.config.strategy.execution_approval, {"action":"submit_entry", "quantity":"2"}):
        with pytest.raises(PaperInputError):
            owner.process(value)
    assert owner.snapshot == before
    from pathlib import Path
    assert not any("AdvancedEntry" in p.read_text(encoding="utf-8") for p in Path("src/quantlab/mcp").rglob("*.py"))


@pytest.mark.parametrize("change", ["price", "sequence", "invalid"])
def test_conflicting_input_identity_fails_without_order_or_financial_mutation(change):
    owner, _ = session(kind="market")
    for item in prefix():
        owner.process(item)
    item = quote(6,"102")
    receipt = owner.process(item)
    before = owner.snapshot
    if change == "price":
        observation = item.observation.model_copy(update={"quote":item.observation.quote.model_copy(update={"ask":D("103")})})
        bad = item.model_copy(update={"observation":observation})
    else:
        bad = item.model_copy(update={"sequence":7 if change == "sequence" else -1})
    with pytest.raises(PaperIdentityConflict):
        owner.process(bad)
    assert owner.snapshot == before and owner.process(item) is receipt
    assert before.account.fees_paid == D("2.2")


@pytest.mark.parametrize("case", ["offset", "late", "future-availability"])
def test_advanced_decisions_obey_causal_feature_and_availability_contracts(case):
    from quantlab.strategies import FeatureOperand, FeatureReference, FeatureType
    from tests.backtesting.helpers import strategy, group, rule
    feature = FeatureReference(feature_id="raw-close",implementation_id="close",feature_type=FeatureType.INDICATOR)
    spec = strategy(no_exit=True,features=(feature,),entry=group(rule(left=FeatureOperand(feature_id=feature.feature_id,offset=1))))
    owner, _ = session(spec=spec)
    owner.process(command("start"))
    item = delivery(sequence=2,delay=MINUTE if case == "late" else 0*MINUTE)
    if case == "future-availability":
        before = owner.snapshot
        item = item.model_copy(update={"bar":item.bar.model_copy(update={"available_at":item.timestamp+MINUTE})})
        with pytest.raises((ValueError,PaperInputError)):
            owner.process(event(item))
        assert owner.snapshot == before
        return
    out = owner.process(event(item))
    assert out.decision.reason == ("late_bar" if case == "late" else "unavailable")
    assert owner.snapshot.runtime.intent is None and owner.snapshot.account.position is None
    if case == "offset":
        out = owner.process(event(delivery(1,sequence=3)))
        assert out.decision.reason == "entry_signal"
        assert out.decision.dependencies == ("bar-close-0","bar-close-1")
        assert all(f.available_at <= out.decision.timestamp for f in out.decision.features)


@pytest.mark.parametrize("partial", [False,True])
def test_input_capacity_still_allows_atomic_stop_and_preserves_position(partial):
    owner, _ = session(kind="market" if partial else "limit",maximum_inputs=3)
    for item in prefix():
        owner.process(item)
    before = owner.snapshot
    with pytest.raises(PaperInputError,match="capacity"):
        owner.process(quote(6,"100"))
    assert owner.snapshot == before
    out = owner.stop(command("stop",6,T+MINUTE))
    assert out.entry_order.state is S.CANCELLED and not out.account.reservations
    assert out.account.fees_paid == (D("1.1") if partial else 0)
    assert out.account.position == before.account.position


@pytest.mark.parametrize("change", ["activation", "price", "liquidity", "age", "fees"])
def test_advanced_approval_cannot_authorize_changed_execution_parameters(change):
    cfg, owners = configuration()
    strategy_cfg = cfg.strategy
    policy = strategy_cfg.entry_policy
    if change == "fees":
        strategy_cfg = strategy_cfg.model_copy(update={"costs":strategy_cfg.costs.model_copy(update={"fixed_fee_per_fill":D("2")})})
    else:
        params = {"activation_policy":"immediate"} if change == "activation" else (
            {"limit_price":D("99")} if change == "price" else
            {"liquidity_per_observation":D("2")} if change == "liquidity" else {"maximum_age":20*MINUTE})
        strategy_cfg = strategy_cfg.model_copy(update={"entry_policy":policy.model_copy(update=params)})
    with pytest.raises((PaperInputError,ValidationError)):
        PaperSession(cfg.model_copy(update={"strategy":strategy_cfg}),**owners)


@pytest.mark.parametrize("stage", ["close", "open", "later"])
def test_standalone_adapter_rejects_reusing_strategy_input_identity(stage):
    from quantlab.paper import StrategyRuntime, StrategyOrderAdapter
    from tests.paper.strategy_helpers import close_quote
    config,owners=configuration()
    account=PaperAccount(config.strategy.account)
    runtime=StrategyRuntime(owners["strategy"],config.strategy,policy=owners["policy"],
        eligibility=owners["eligibility"],evidence=owners["evidence"],account_snapshot=account.snapshot)
    d=delivery(sequence=2)
    runtime.process(d)
    adapter=StrategyOrderAdapter(runtime,account)
    close=close_quote(d,sequence=3)
    if stage=="close":
        bad=close.model_copy(update={"event_id":d.event_id})
        before=account.snapshot
        with pytest.raises(PaperIdentityConflict): adapter.submit(bad)
    else:
        adapter.submit(close)
        op=opening(d,sequence=5)
        if stage=="open":
            bad=op.model_copy(update={"event_id":d.event_id})
            before=account.snapshot
            with pytest.raises(PaperIdentityConflict): adapter.process_open(bad)
        else:
            adapter.process_open(op)
            before=account.snapshot
            bad=quote(6,"100").observation.model_copy(update={"event_id":d.event_id})
            with pytest.raises(PaperIdentityConflict): adapter.process_quote(bad)
    assert account.snapshot==before and account.snapshot.fees_paid==0


@pytest.mark.parametrize("kind", ["market","limit","stop_market","stop_limit"])
def test_short_advanced_entry_attribution_and_single_fill_fee(kind):
    from quantlab.strategies import Direction
    from tests.backtesting.helpers import strategy
    owner,_=session(kind=kind,budget=None,spec=strategy(direction=Direction.SHORT,no_exit=True))
    for item in prefix(close=99):
        owner.process(item)
    if kind=="stop_market":
        owner.process(quote(6,"101"))
    elif kind=="stop_limit":
        owner.process(quote(6,"105"))
    snap=owner.snapshot
    assert snap.execution.kernel.state is S.FILLED
    assert snap.account.position.direction.value=="short" and snap.account.position.quantity==2
    assert snap.account.fees_paid==D("1.2")
    assert snap.runtime.intent.side.value=="sell"
