"""Bounded causal bar-close evaluation. No transport, inference or order authority."""
from dataclasses import dataclass
from itertools import islice

from quantlab.backtesting import EvaluationResult as E
from quantlab.backtesting.evaluation import RuleEvaluator
from quantlab.data import ValidationOptions, validate_dataset
from quantlab.features import compute_features, validate_strategy_features
from quantlab.strategies import Comparison, FeatureOperand, MarketOperand, StrategySpecification
from .admission import AdmissionError, admit_strategy
from .errors import PaperIdentityConflict, PaperInputError
from .models import OrderSide
from .strategy_models import (
    BarCloseDelivery, EntryIntent, RuntimeSnapshot, StrategyDecision,
    StrategySessionConfig, record,
)


@dataclass(frozen=True)
class _Publication:
    last_sequence: int
    timestamp: object
    count: int
    history: tuple[BarCloseDelivery, ...]
    features: tuple
    intent: EntryIntent | None


class _CausalBars:
    """Read-only fixed-length evaluator view; never copy the retained bar journal."""
    def __init__(self, inputs, count, current):
        self._inputs, self._count, self._current = inputs, count, current

    def __len__(self):
        return self._count + 1

    def __getitem__(self, index):
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self._current.bar if index == self._count else self._inputs[index].bar


class StrategyRuntime:
    """Trusted local construction rechecks admission; records alone cannot activate.

    Single serialized caller, bounded journals and indexed rule dependencies. Public
    snapshots materialize immutable tuples only on request, not on every event.
    Missing offsets retain UNAVAILABLE; raw features are derived from delivered
    completed bars. No caller feature override or retrospective batch input exists.
    """
    def __init__(self, strategy, config, *, policy, eligibility, evidence, account_snapshot):
        admission = admit_strategy(strategy, config, policy=policy, eligibility=eligibility,
                                   evidence=evidence, account_snapshot=account_snapshot)
        if not admission.admitted:
            raise AdmissionError(admission)
        self._strategy = StrategySpecification.model_validate(strategy)
        self._config = StrategySessionConfig.model_validate(config)
        self._admission = admission
        self._requests = validate_strategy_features(self._strategy)
        self._groups = tuple(side.entry for side in (self._strategy.content.long,
            self._strategy.content.short) if side is not None)
        self._feature_index = {}
        self._publication = _Publication(0, self._config.timestamp, 0, (), (), None)
        self._seen, self._inputs, self._decisions = {}, [], []
        self._busy = False

    @property
    def config(self):
        return self._config

    @property
    def admission(self):
        return self._admission

    @property
    def snapshot(self):
        p = self._publication
        return RuntimeSnapshot(admission=self._admission, config=self._config,
            last_sequence=p.last_sequence, timestamp=p.timestamp,
            state="active" if p.intent is None else "entry_intent_emitted",
            inputs=tuple(islice(self._inputs, p.count)),
            decisions=tuple(islice(self._decisions, p.count)), intent=p.intent)

    def _stage_indexes(self, delivery, wire, decision):
        self._inputs.append(delivery)
        self._decisions.append(decision)
        self._seen[delivery.event_id] = (wire, decision)
        for feature in decision.features:
            if feature.timestamp == delivery.bar.end_time:
                self._feature_index[(feature.feature_id, feature.timestamp)] = feature

    def process(self, delivery: BarCloseDelivery) -> StrategyDecision:
        if self._busy:
            raise PaperInputError("runtime event processing already in progress")
        self._busy = True
        try:
            return self._process(delivery)
        finally:
            self._busy = False

    def _process(self, delivery):
        if type(delivery) is not BarCloseDelivery:
            raise PaperInputError("expected a canonical completed-bar delivery")
        identity = delivery.event_id
        try:
            delivery = BarCloseDelivery.model_validate(delivery)
            wire = delivery.canonical_json()
        except (ValueError, TypeError) as exc:
            if type(identity) is str and identity in self._seen:
                raise PaperIdentityConflict("retained bar identity reused with invalid content") from exc
            raise PaperInputError("invalid completed-bar delivery") from exc
        prior = self._seen.get(identity)
        if prior is not None:
            if prior[0] != wire:
                raise PaperIdentityConflict("bar identity reused with different content")
            return prior[1]
        old, cfg, bar = self._publication, self._config, delivery.bar
        if old.count >= cfg.maximum_events:
            raise PaperInputError("runtime event retention capacity reached")
        if delivery.sequence <= old.last_sequence or delivery.timestamp < old.timestamp:
            raise PaperInputError("bar delivery sequence/time cannot go backwards")
        if (bar.instrument_id != cfg.account.instrument.instrument_id
                or bar.timeframe is not cfg.timeframe or bar.price_type is not cfg.price_type):
            raise PaperInputError("bar series does not match the admitted session")
        if old.history:
            previous = old.history[-1]
            if delivery.delivered_at < previous.delivered_at:
                raise PaperInputError("bar delivery chronology cannot go backwards")
            bars = (previous.bar, bar)
        else:
            bars = (bar,)
        quality = validate_dataset(bars, instrument=cfg.account.instrument,
            options=ValidationOptions(homogeneous_source=False))
        if not quality.valid:
            raise PaperInputError("invalid chronological canonical bar series")
        history = (*old.history, delivery)[-2:]
        current_features = compute_features((bar,), self._requests, instrument=cfg.account.instrument)
        # Resolve only declared rule dependencies (including crossing history).
        # RuleEvaluator owns all comparisons and availability semantics unchanged.
        bars_view = _CausalBars(self._inputs, old.count, delivery)
        index = old.count
        dependency_indices = {index}
        features_by_key = {(f.feature_id, f.timestamp): f for f in current_features}
        for group in self._groups:
            for rule in group.rules:
                crossing = rule.comparison in (Comparison.CROSSES_ABOVE, Comparison.CROSSES_BELOW)
                for operand in (rule.left, rule.right):
                    if not isinstance(operand, (MarketOperand, FeatureOperand)):
                        continue
                    for previous in ((0, 1) if crossing else (0,)):
                        target = index - operand.offset - previous
                        if target < 0:
                            continue
                        dependency_indices.add(target)
                        if isinstance(operand, FeatureOperand):
                            key = (operand.feature_id, bars_view[target].end_time)
                            feature = features_by_key.get(key) or self._feature_index.get(key)
                            if feature is not None:
                                features_by_key[key] = feature
        features = tuple(features_by_key[k] for k in sorted(features_by_key, key=lambda k: (k[1], k[0])))
        dependencies = tuple(delivery.event_id if i == index else self._inputs[i].event_id
                             for i in sorted(dependency_indices))
        evaluator = RuleEvaluator(self._strategy, bars_view, features)
        long = evaluator.evaluate(self._strategy.content.long.entry, index) if self._strategy.content.long else None
        short = evaluator.evaluate(self._strategy.content.short.entry, index) if self._strategy.content.short else None
        side = None
        if delivery.timestamp != bar.end_time or delivery.delivered_at != bar.end_time:
            # A complete bar delivered late cannot generate a retrospective signal.
            long = E.UNAVAILABLE if long is not None else None
            short = E.UNAVAILABLE if short is not None else None
            reason = "late_bar"
        elif long is E.TRUE and short is E.TRUE:
            reason = "conflicting_signals"
        elif old.intent is not None:
            reason = "single_entry_consumed"
        elif long is E.TRUE or short is E.TRUE:
            reason = "entry_signal"
            side = OrderSide.BUY if long is E.TRUE else OrderSide.SELL
        elif E.UNAVAILABLE in (long, short):
            reason = "unavailable"
        else:
            reason = "no_signal"
        common = dict(strategy_id=cfg.strategy_id, strategy_version=cfg.strategy_version,
            strategy_digest=cfg.strategy_digest, session_id=cfg.session_id,
            account_id=cfg.account.account_id, admission_id=self._admission.record_id,
            policy_digest=self._admission.policy_digest, sequence=delivery.sequence,
            timestamp=delivery.timestamp, causation_id=identity)
        decision = record(StrategyDecision, **common, source_bar_start=bar.start_time,
            source_bar_end=bar.end_time, long=long, short=short,
            dependencies=dependencies, features=features, reason=reason, side=side)
        intent = old.intent
        if side is not None:
            intent = record(EntryIntent, **common, decision_id=decision.record_id,
                instrument_id=bar.instrument_id, side=side, quantity=cfg.quantity,
                source_bar_start=bar.start_time, source_bar_end=bar.end_time)
        updated = _Publication(delivery.sequence, delivery.timestamp, old.count + 1,
                               history, current_features, intent)
        try:
            self._stage_indexes(delivery, wire, decision)
            self._publication = updated
        except BaseException:
            object.__setattr__(self, "_publication", old)
            del self._inputs[old.count:]
            del self._decisions[old.count:]
            self._seen.pop(identity, None)
            for feature in current_features:
                self._feature_index.pop((feature.feature_id, feature.timestamp), None)
            raise
        return decision

    def retained_intent(self) -> EntryIntent:
        if self._publication.intent is None:
            raise PaperInputError("runtime has no retained entry intent")
        return self._publication.intent
