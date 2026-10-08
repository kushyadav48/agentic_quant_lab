"""Exclusive serialized in-memory session owner, reusing Phase 18A–18C engines.

Private candidates may publish internally, but no candidate owner is exposed.
Only the session root swap publishes financial, opening, feed and audit state.
Runtime journals share append-only storage: old public views use committed counts.
Failed candidates remove their suffix and incremental indexes before exact retry.
This is not a concurrent reader/writer or durable transaction service.
"""
from copy import copy
from dataclasses import dataclass, replace
from itertools import islice

from .accounts import PaperAccount
from .errors import PaperIdentityConflict, PaperInputError
from .feed import accept_delivery, advance_clock, feed_reason
from .models import CancellationRequest, OrderState, stable_id
from .runtime import StrategyRuntime
from .session_models import (ClockState, FeedState, ReplayConfig, ReplayEvent,
    SessionCommand, SessionRecord, SessionSnapshot, SessionState as S)
from .strategy_models import record
from .strategy_orders import StrategyOrderAdapter


@dataclass(frozen=True)
class _SessionPublication:
    state: S
    clock: ClockState
    feed: FeedState
    runtime: StrategyRuntime
    account: PaperAccount
    adapter: StrategyOrderAdapter
    count: int = 0
    record_id: str | None = None


class PaperSession:
    """Constructs fresh financial owners and rechecks admission, never restores.

    Inputs are trusted local producers' records, not an authenticated wire API.
    One caller serializes all operations; snapshots alone confer no authority.
    """
    def __init__(self, config: ReplayConfig, *, strategy, policy, eligibility, evidence):
        self._config = ReplayConfig.model_validate(config)
        self._config_digest = stable_id("paper-replay-config-v1", self._config)
        account = PaperAccount(self._config.strategy.account)
        runtime = StrategyRuntime(strategy, self._config.strategy, policy=policy,
            eligibility=eligibility, evidence=evidence, account_snapshot=account.snapshot)
        adapter = StrategyOrderAdapter(runtime, account)
        self._publication = _SessionPublication(S.CREATED,
            ClockState(timestamp=self._config.strategy.timestamp), FeedState(),
            runtime, account, adapter)
        self._seen, self._records = {}, []
        self._busy = False

    @property
    def config(self):
        return self._config

    @property
    def snapshot(self):
        p = self._publication
        return SessionSnapshot(config=self._config, state=p.state, clock=p.clock,
            feed=p.feed, account=p.account.snapshot, runtime=p.runtime.snapshot,
            execution=p.adapter.audit_snapshot,
            records=tuple(islice(self._records, p.count)))

    @property
    def records(self):
        return tuple(islice(self._records, self._publication.count))

    def start(self, command):
        return self._command(command, "start")

    def pause(self, command):
        return self._command(command, "pause")

    def resume(self, command):
        return self._command(command, "resume")

    def stop(self, command):
        return self._command(command, "stop")

    def _command(self, command, action):
        if type(command) is not SessionCommand or command.action != action:
            raise PaperInputError("command action does not match session method")
        return self.process(command)

    def _candidate_owners(self, before):
        runtime, account, kernel, adapter = (copy(before.runtime), copy(before.account),
            copy(before.adapter._kernel), copy(before.adapter))
        # Financial caches stay small: one entry, reservation and terminal outcome.
        # Runtime history is never copied or serialized during an event update.
        for name in ("_seen", "_adapter_seen"):
            setattr(account, name, dict(getattr(account, name)))
        account._journal = list(account._journal)
        for name in ("_reservation_ids", "_reserved_orders", "_settled_orders", "_execution_ids"):
            setattr(account, name, set(getattr(account, name)))
        kernel._account_owner = account
        account._kernels = {runtime.config.session_id: (kernel, runtime.config.strategy_id)}
        adapter._runtime, adapter._account, adapter._kernel = runtime, account, kernel
        return runtime, account, adapter

    def _prepare_record(self, **values):
        return record(SessionRecord, **values)

    def _commit_candidate(self, item, outcome, candidate):
        """Internal durability seam: required records precede public state.

        Standalone sessions retain in-memory behavior. A durable owner overrides
        this only at the exclusive serialized session boundary.
        """

    def _publish_candidate(self, candidate):
        self._publication = candidate

    def _stage_record(self, identity, wire, outcome):
        self._records.append(outcome)
        self._seen[identity] = (wire, outcome)

    @staticmethod
    def _rollback_runtime(runtime, count):
        for delivery, decision in zip(runtime._inputs[count:], runtime._decisions[count:]):
            runtime._seen.pop(delivery.event_id, None)
            for feature in decision.features:
                if feature.timestamp == delivery.bar.end_time:
                    runtime._feature_index.pop((feature.feature_id, feature.timestamp), None)
        del runtime._inputs[count:]
        del runtime._decisions[count:]

    def process(self, item):
        if self._busy:
            raise PaperInputError("session processing already in progress")
        self._busy = True
        try:
            return self._process(item)
        finally:
            self._busy = False

    def _process(self, item):
        if type(item) not in (ReplayEvent, SessionCommand):
            raise PaperInputError("canonical replay event or session command required")
        identity = item.event_id if type(item) is ReplayEvent else item.command_id
        try:
            item = type(item).model_validate(item)
            wire = item.canonical_json()
        except (ValueError, TypeError) as exc:
            if type(identity) is str and identity in self._seen:
                raise PaperIdentityConflict("retained session identity reused with invalid content") from exc
            raise PaperInputError("invalid session input") from exc
        prior = self._seen.get(identity)
        if prior is not None:
            if wire != prior[0]:
                raise PaperIdentityConflict("session input identity reused with different content")
            return prior[1]
        before = self._publication
        terminal = type(item) is SessionCommand and item.action in ("stop", "fail")
        if before.count >= self._config.maximum_inputs and not terminal:
            raise PaperInputError("session retention capacity reached")
        clock = advance_clock(before.clock, item)
        if before.state in (S.STOPPED, S.FAILED):
            raise PaperInputError("terminated session accepts only exact retries")
        if type(item) is ReplayEvent:
            if before.state is S.CREATED:
                raise PaperInputError("start session before feed delivery")
            if item.provenance not in self._config.sources:
                raise PaperInputError("unbound feed provenance")
            if item.observation is not None:
                obs = item.observation
                market = obs.bar if item.kind == "bar_close" else obs.quote
                if market.instrument_id != self._config.strategy.account.instrument.instrument_id:
                    raise PaperInputError("feed instrument differs from session")
            feed = accept_delivery(before.feed, item, self._config.stale)
        else:
            feed = before.feed.model_copy(update={"reason":
                feed_reason(before.feed, clock.timestamp, self._config.stale)})
        runtime, account, adapter = self._candidate_owners(before)
        count = before.runtime._publication.count
        try:
            state, transitions, decision, orders = before.state, (), None, ()
            financial_count = account.snapshot.event_sequence
            if type(item) is SessionCommand:
                legal = {"start": (S.CREATED, S.ACTIVE, "started"),
                    "pause": (S.ACTIVE, S.PAUSED, "paused"),
                    "resume": (S.PAUSED, S.ACTIVE, "resumed")}
                if item.action in legal:
                    required, state, reason = legal[item.action]
                    if before.state is not required:
                        raise PaperInputError("invalid session lifecycle transition")
                    transitions = (state,)
                else:
                    if item.action == "fail" and before.state is S.CREATED:
                        raise PaperInputError("only a started session can fail")
                    k = adapter.snapshot
                    if k.state is OrderState.ACCEPTED:
                        orders = account.process_order(adapter._kernel, CancellationRequest(
                            command_id=stable_id("paper-session-stop-cancel-v1", item),
                            causation_id=k.submission.command_id, order_id=k.order_id,
                            sequence=max(k.last_sequence + 1, item.sequence), timestamp=item.timestamp))
                    if account.snapshot.reservations:
                        raise PaperInputError("termination cannot leave reservations")
                    state = S.FAILED if item.action == "fail" else S.STOPPED
                    reason = "failed" if item.action == "fail" else "stopped"
                    transitions = (S.STOPPING, state)
            elif before.state is S.PAUSED:
                reason = "inactive"
            elif feed.reason != "fresh":
                reason = feed.reason
            elif item.kind == "bar_close":
                decision = runtime.process(item.observation)
                reason = "evaluated"
            elif item.kind == "quote":
                if runtime._publication.intent is None:
                    reason = "observed"
                elif adapter.snapshot.submission is None:
                    orders = adapter.submit(item.observation)
                    reason = "submitted"
                else:
                    reason = "terminal_order" if adapter.snapshot.terminated else "observed"
            elif item.kind == "opening":
                if runtime._publication.intent is None:
                    raise PaperInputError("opening lacks a causal entry intent")
                orders = adapter.process_open(item.observation)
                reason = "opening_processed"
            else:
                reason = "observed"
            financial = tuple(islice(account._journal, financial_count, account.snapshot.event_sequence))
            outcome = self._prepare_record(session_id=self._config.strategy.session_id,
                config_digest=self._config_digest, input_id=identity,
                input_digest=stable_id("paper-session-input-v1", item),
                sequence=item.sequence, timestamp=item.timestamp, state=state,
                transitions=transitions, reason=reason,
                reason_reference=item.reason_reference if type(item) is SessionCommand else None,
                market_event=item if type(item) is ReplayEvent else None,
                command=item if type(item) is SessionCommand else None,
                decision=decision, orders=orders, financial=financial, account=account.snapshot,
                feed=feed, previous_record_id=before.record_id)
            candidate = replace(before, state=state, clock=clock, feed=feed, runtime=runtime,
                account=account, adapter=adapter, count=before.count + 1, record_id=outcome.record_id)
            self._stage_record(identity, wire, outcome)
            self._commit_candidate(item, outcome, candidate)
            self._publish_candidate(candidate)
        except BaseException:
            object.__setattr__(self, "_publication", before)
            del self._records[before.count:]
            self._seen.pop(identity, None)
            self._rollback_runtime(runtime, count)
            raise
        return outcome

    def replay(self, events, *, maximum_events):
        """Consume at most the explicit bound, in supplied order, without lookahead.

        A batch is not a transaction: each preceding accepted event stays committed.
        Exhaustion is recorded explicitly by the producer, never inferred from EOF.
        """
        if type(maximum_events) is not int or not 0 <= maximum_events <= self._config.maximum_inputs:
            raise PaperInputError("invalid replay processing bound")
        return tuple(self.process(event) for event in islice(events, maximum_events))
