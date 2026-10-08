"""Causal delivery, tri-state rules, immutable history and bounded processing."""
from datetime import timedelta
from decimal import Context, Inexact, Rounded, ROUND_DOWN, localcontext
import socket

import pytest
from pydantic import ValidationError
from quantlab.backtesting import EvaluationResult as E
from quantlab.data import PriceType
from quantlab.paper import (
    BarCloseDelivery, PaperIdentityConflict, PaperInputError, StrategyOrderAdapter,
    StrategyRuntime, stable_id,
)
from quantlab.paper.strategy_models import record
from quantlab.strategies import (
    Comparison, ConstantOperand, Direction, FeatureOperand, FeatureReference,
    FeatureType, GroupMode, MarketField, MarketOperand,
)
from tests.backtesting.helpers import D, MINUTE, group, rule, strategy
from .strategy_helpers import close_quote, delivery, opening, runtime


def test_bar_close_decision_and_exact_strategy_attribution():
    rt, account, args, ev = runtime()
    d = delivery()
    decision = rt.process(d)
    intent = rt.snapshot.intent
    assert decision.timestamp == d.bar.end_time
    assert decision.long is E.TRUE and decision.short is None
    assert decision.reason == "entry_signal"
    assert intent.decision_id == decision.record_id
    assert intent.causation_id == d.event_id
    for item in (decision, intent):
        assert (item.strategy_id, item.strategy_version, item.strategy_digest) == (
            rt.config.strategy_id, rt.config.strategy_version, rt.config.strategy_digest)
        assert item.account_id == account.snapshot.config.account_id
        assert item.session_id == rt.config.session_id
        assert item.admission_id == rt.admission.record_id
        assert item.policy_digest == args["policy"].digest
    assert account.snapshot == args["account_snapshot"]  # Evaluation has no execution authority.


@pytest.mark.parametrize("comparison", list(Comparison))
def test_existing_comparison_semantics_and_crossing_history(comparison):
    spec = strategy(no_exit=True, entry=group(rule(comparison)))
    rt, _, _, _ = runtime(spec=spec)
    first = rt.process(delivery(close=99))
    second = rt.process(delivery(1, close=101))
    if comparison in (Comparison.CROSSES_ABOVE, Comparison.CROSSES_BELOW):
        assert first.long is E.UNAVAILABLE
    expected = E.TRUE if comparison in (Comparison.GT, Comparison.GE, Comparison.CROSSES_ABOVE) else E.FALSE
    assert second.long is expected


@pytest.mark.parametrize("mode,first,second", [(GroupMode.ALL, E.UNAVAILABLE, E.TRUE),
    (GroupMode.ANY, E.TRUE, E.TRUE)])
def test_tri_state_groups_preserve_unavailable_offsets(mode, first, second):
    spec = strategy(no_exit=True, entry=group(rule(offset=1), rule(), mode=mode))
    rt, _, _, _ = runtime(spec=spec)
    assert rt.process(delivery()).long is first
    assert rt.process(delivery(1)).long is second


def test_false_dominates_unavailable_for_all_group():
    spec = strategy(no_exit=True, entry=group(rule(offset=3), rule(Comparison.LT)))
    rt, _, _, _ = runtime(spec=spec)
    assert rt.process(delivery()).long is E.FALSE
    assert rt.snapshot.intent is None


def test_raw_feature_alias_is_derived_only_from_delivered_completed_bar():
    f = FeatureReference(feature_id="known_close", implementation_id="close", feature_type=FeatureType.INDICATOR)
    spec = strategy(no_exit=True, features=(f,), entry=group(rule(left=FeatureOperand(feature_id=f.feature_id))))
    rt, _, _, _ = runtime(spec=spec)
    d = delivery()
    decision = rt.process(d)
    assert decision.long is E.TRUE
    observation = decision.features[0]
    assert observation.value == d.bar.close
    assert observation.input_start == d.bar.start_time
    assert observation.timestamp == observation.available_at == d.bar.end_time
    assert decision.dependencies == (d.event_id,)
    with pytest.raises(TypeError): rt.process(d, features=(observation,))


@pytest.mark.parametrize("delay", [timedelta(microseconds=1), MINUTE])
def test_late_complete_bar_cannot_create_retrospective_signal(delay):
    rt, _, _, _ = runtime()
    decision = rt.process(delivery(delay=delay))
    assert decision.reason == "late_bar" and decision.long is E.UNAVAILABLE
    assert rt.snapshot.intent is None


def test_delayed_dependency_never_appears_before_available_at():
    spec = strategy(no_exit=True, entry=group(rule(offset=1)))
    rt, _, _, _ = runtime(spec=spec)
    first = rt.process(delivery(delay=MINUTE))
    second = rt.process(delivery(1))
    assert first.long is E.UNAVAILABLE
    assert second.long is E.TRUE  # Prior bar is known now; the first decision is unchanged.
    assert rt.snapshot.decisions[0] == first


@pytest.mark.parametrize("kind", ["early_processing", "early_delivery", "wrong_instrument", "wrong_basis",
                                  "future_availability", "negative_sequence", "mutable_dict"])
def test_invalid_market_event_leaves_state_unchanged(kind):
    rt, _, _, _ = runtime()
    d, before = delivery(), rt.snapshot
    if kind == "early_processing": bad = d.model_copy(update={"timestamp": d.bar.start_time})
    elif kind == "early_delivery": bad = d.model_copy(update={"delivered_at": d.bar.start_time})
    elif kind == "wrong_instrument": bad = d.model_copy(update={"bar": d.bar.model_copy(update={"instrument_id": "other"})})
    elif kind == "wrong_basis": bad = d.model_copy(update={"bar": d.bar.model_copy(update={"price_type": PriceType.TRADE})})
    elif kind == "future_availability": bad = d.model_copy(update={"bar": d.bar.model_copy(update={"available_at": d.timestamp+MINUTE})})
    elif kind == "negative_sequence": bad = d.model_copy(update={"sequence": -1})
    else: bad = d.model_dump()
    with pytest.raises(PaperInputError): rt.process(bad)
    assert rt.snapshot == before
    assert rt.process(d).long is E.TRUE


def test_identity_retry_and_conflict_cannot_duplicate_decisions_or_intents():
    rt, _, _, _ = runtime()
    d = delivery()
    result = rt.process(d)
    before = rt.snapshot
    assert rt.process(d) == result
    assert rt.snapshot == before
    for bad in (delivery(close=110), d.model_copy(update={"sequence": True})):
        with pytest.raises(PaperIdentityConflict): rt.process(bad)
        assert rt.snapshot == before


@pytest.mark.parametrize("kind", ["new_id_same_bar", "older_sequence", "backwards_time", "overlap"])
def test_late_or_reordered_data_cannot_rewrite_history(kind):
    rt, _, _, _ = runtime()
    first = delivery()
    result = rt.process(first)
    before = rt.snapshot
    if kind == "new_id_same_bar": bad = first.model_copy(update={"event_id": "new", "sequence": 2})
    elif kind == "older_sequence": bad = delivery(1, sequence=1)
    elif kind == "backwards_time": bad = delivery(1).model_copy(update={"timestamp": first.timestamp-MINUTE})
    else:
        d = delivery(1)
        bad = d.model_copy(update={"bar": d.bar.model_copy(update={"start_time": first.bar.start_time})})
    with pytest.raises(PaperInputError): rt.process(bad)
    assert rt.snapshot == before
    assert rt.snapshot.decisions[0] == result


def test_future_deliveries_do_not_change_prior_immutable_decisions():
    left, _, _, _ = runtime()
    right, _, _, _ = runtime()
    d = delivery(close=99)
    saved = left.process(d)
    assert right.process(d) == saved
    old_wire = left.snapshot.canonical_json()
    left.process(delivery(1, close=200))
    right.process(delivery(1, close=1))
    assert left.snapshot.decisions[0] == right.snapshot.decisions[0] == saved
    assert saved.canonical_json() in old_wire


def test_repeated_true_signals_are_bounded_to_one_entry():
    rt, _, _, _ = runtime()
    first = rt.process(delivery())
    intent = rt.snapshot.intent
    second = rt.process(delivery(1))
    assert first.reason == "entry_signal" and second.reason == "single_entry_consumed"
    assert second.long is E.TRUE and second.side is None
    assert rt.snapshot.intent == intent


def test_conflicting_long_short_entries_emit_no_intent():
    spec = strategy(direction=Direction.BOTH, no_exit=True,
        entry=group(rule(Comparison.GT, right=ConstantOperand(value=D("200")))))
    rt, _, _, _ = runtime(spec=spec)
    result = rt.process(delivery(close=201))
    assert result.long is result.short is E.TRUE
    assert result.reason == "conflicting_signals"
    assert rt.snapshot.intent is None


def test_runtime_failure_atomicity_and_retry(monkeypatch):
    rt, _, _, _ = runtime()
    before = rt.snapshot
    original = rt._stage_indexes
    def fail(*args):
        original(*args)
        assert rt.snapshot == before
        raise RuntimeError("injected staging failure")
    monkeypatch.setattr(rt, "_stage_indexes", fail)
    with pytest.raises(RuntimeError): rt.process(delivery())
    assert rt.snapshot == before
    monkeypatch.setattr(rt, "_stage_indexes", original)
    assert rt.process(delivery()).long is E.TRUE


def test_failed_evaluation_creates_neither_decision_nor_order(monkeypatch):
    import quantlab.paper.runtime as module
    rt, account, _, _ = runtime()
    before = rt.snapshot
    def fail(*args): raise RuntimeError("evaluation failure")
    monkeypatch.setattr(module.RuleEvaluator, "evaluate", fail)
    with pytest.raises(RuntimeError): rt.process(delivery())
    assert rt.snapshot == before
    assert account.events == ()
    with pytest.raises(PaperInputError): rt.retained_intent()


def test_runtime_snapshots_records_and_nested_features_are_frozen_and_round_trip():
    rt, _, args, ev = runtime()
    rt.process(delivery())
    models = (rt.snapshot, rt.snapshot.decisions[0], rt.snapshot.intent, rt.admission,
              rt.config, args["policy"], args["eligibility"], *ev)
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model
        with pytest.raises(ValidationError): setattr(model, next(iter(type(model).model_fields)), None)
        with pytest.raises(ValidationError): type(model).model_validate({**model.model_dump(), "extra": True})


def test_decimal_context_and_isolated_instances_do_not_change_financial_decisions():
    rt, _, _, _ = runtime()
    expected = rt.process(delivery())
    with localcontext(Context(prec=2, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = context.traps[Rounded] = True
        another, _, _, _ = runtime()
        actual = another.process(delivery())
        assert actual == expected and actual.canonical_json() == expected.canonical_json()
        assert context.prec == 2 and context.traps[Inexact] and context.traps[Rounded]
    isolated, _, _, _ = runtime()
    assert isolated.snapshot.decisions == ()
    assert isolated.snapshot.intent is None


def test_runtime_has_no_network_side_effects(monkeypatch):
    def deny(*args, **kwargs): raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    rt, account, _, _ = runtime()
    d = delivery()
    rt.process(d)
    adapter = StrategyOrderAdapter(rt, account)
    adapter.submit(close_quote(d))
    adapter.process_open(opening(d))
    assert account.snapshot.position is not None


def test_event_capacity_preserves_retries_and_rejects_new_input_atomically():
    rt, _, _, _ = runtime(maximum_events=1)
    d = delivery()
    decision = rt.process(d)
    before = rt.snapshot
    with pytest.raises(PaperInputError, match="capacity"): rt.process(delivery(1))
    assert rt.snapshot == before
    assert rt.process(d) == decision


def test_bounded_lookback_and_no_full_journal_serialization_per_event(monkeypatch):
    from quantlab.paper import RuntimeSnapshot
    from quantlab.features import pipeline
    rt, _, _, _ = runtime(maximum_events=250)
    def deny(*args, **kwargs): raise AssertionError("full snapshot encoding in event path")
    monkeypatch.setattr(RuntimeSnapshot, "canonical_json", deny)
    original = pipeline.compute_features
    import quantlab.paper.runtime as module
    calls = []
    def bounded(bars, *args, **kwargs):
        calls.append(len(bars))
        return original(bars, *args, **kwargs)
    monkeypatch.setattr(module, "compute_features", bounded)
    for i in range(250): rt.process(delivery(i, close=99))
    assert calls == [1] * 250
    assert len(rt._publication.history) == 2
    assert len(rt.snapshot.decisions) == 250


def test_large_offsets_use_indexed_dependencies_without_copying_full_history():
    spec = strategy(no_exit=True, entry=group(rule(offset=100)))
    rt, _, _, _ = runtime(spec=spec, maximum_events=150)
    for i in range(150):
        decision = rt.process(delivery(i, close=101))
        assert len(decision.dependencies) <= 2
        assert decision.long is (E.UNAVAILABLE if i < 100 else E.TRUE)
    assert len(rt._publication.history) == 2
    assert decision.dependencies == ("bar-close-49", "bar-close-149")
