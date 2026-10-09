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
from .models import (CancellationRequest, CancellationAcknowledgement, OrderState, stable_id,
    MarketDelivery, AdvancedOrderSubmission, OrderSide)
from .orders import PaperOrderKernel
from .oco_models import OCOCommand
from .runtime import StrategyRuntime
from .session_models import (ClockState, FeedState, ReplayConfig, ReplayEvent,
    SessionCommand, SessionRecord, SessionSnapshot, SessionState as S, AdvancedReplayConfig, AdvancedSessionCommand, AdvancedEntryReplayConfig, EntryCancellationCommand)
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
    exit_kernel: PaperOrderKernel | None = None


class PaperSession:
    """Constructs fresh financial owners and rechecks admission, never restores.

    Inputs are trusted local producers' records, not an authenticated wire API.
    One caller serializes all operations; snapshots alone confer no authority.
    """
    def __init__(self, config: ReplayConfig, *, strategy, policy, eligibility, evidence):
        self._config = (AdvancedEntryReplayConfig if type(config) is AdvancedEntryReplayConfig else
            AdvancedReplayConfig if type(config) is AdvancedReplayConfig else ReplayConfig).model_validate(config)
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
            records=tuple(islice(self._records, p.count)),
            exit_order=None if p.exit_kernel is None else p.exit_kernel.snapshot, oco=p.account.oco_snapshot)

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
        # Journals/indexes share append-only storage, like runtime history.
        # Public reads select committed counts; failures undo only this suffix.
        account._cache_undo = []
        kernel._account_owner = account
        account._kernels = {}
        for session, (existing, strategy) in before.account._kernels.items():
            cloned = kernel if existing is before.adapter._kernel else copy(existing)
            cloned._account_owner = account
            account._kernels[session] = (cloned, strategy)
        adapter._runtime, adapter._account, adapter._kernel = runtime, account, kernel
        return runtime, account, adapter

    def _exit_command(self, account, before, item):
        """Stage only local position-linked orders; caller cannot supply fills/risk."""
        if not isinstance(self.config, AdvancedReplayConfig) or before.state is not S.ACTIVE:
            raise PaperInputError("advanced exit requires an active v2 session")
        current = None if before.exit_kernel is None else account._kernels[before.exit_kernel._config.session_id][0]
        if item.action != "submit_exit":
            if current is None or current._view.submission.position_id != item.position_id:
                raise PaperInputError("cancellation does not own the active exit")
            k = current._view
            cls = CancellationRequest if item.action == "request_cancel_exit" else CancellationAcknowledgement
            cause = k.submission.command_id
            if cls is CancellationAcknowledgement and k.pending_cancellation is not None:
                cause = k.pending_cancellation.request_id
            records = current.process(cls(command_id=item.command_id, causation_id=cause,
                sequence=item.sequence, timestamp=item.timestamp, order_id=k.order_id))
            return current, records, "exit_cancellation"
        position = account.snapshot.position
        if (position is None or position.quantity == 0 or item.position_id != position.entry_transaction_id
                or position.strategy_id != self.config.strategy.strategy_id or item.quantity > position.quantity
                or before.adapter._kernel._view.state is not OrderState.FILLED):
            raise PaperInputError("exit must own the committed strategy position")
        if current is not None and not current._view.terminated:
            raise PaperInputError("one active protective child/exit; OCO is unsupported")
        if before.feed.reason != "fresh" or item.timestamp - position.valuation.quote.timestamp > self.config.stale.maximum_age:
            raise PaperInputError("exit activation requires fresh position valuation")
        session_id = stable_id("paper-position-exit-kernel-v2", (self.config.strategy.session_id, item.command_id))
        current = account.create_advanced_order_kernel(session_id=session_id,
            strategy_id=position.strategy_id, risk=self.config.strategy.risk, costs=self.config.strategy.costs,
            liquidity_per_observation=self.config.liquidity_per_observation,
            maximum_age=self.config.stale.maximum_age, reduce_only=True)
        seed = MarketDelivery(sequence=item.sequence - 1, timestamp=item.timestamp,
            event_id=stable_id("paper-position-exit-reference-v2", item),
            delivered_at=position.valuation.delivered_at, quote=position.valuation.quote)
        current.process(seed)
        side = OrderSide.SELL if position.direction.value == "long" else OrderSide.BUY
        command = AdvancedOrderSubmission(sequence=item.sequence, timestamp=item.timestamp,
            command_id=item.command_id, causation_id=seed.event_id,
            instrument_id=position.instrument.instrument_id, side=side, quantity=item.quantity,
            order_type=item.order_type, time_in_force=item.time_in_force,
            limit_price=item.limit_price, stop_price=item.stop_price, reduce_only=True,
            position_id=item.position_id, protective_role=item.protective_role)
        return current, current.process(command), "exit_submitted"

    def _entry_cancel(self, adapter, before, item):
        if type(self.config) is not AdvancedEntryReplayConfig or before.state is not S.ACTIVE:
            raise PaperInputError("entry cancellation requires active advanced entry session")
        intent = adapter._runtime.retained_intent()
        k = adapter._kernel._view
        if item.intent_id != intent.record_id or k.submission is None:
            raise PaperInputError("entry cancellation must bind the admitted submitted intent")
        cls = CancellationRequest if item.action == "request_cancel_entry" else CancellationAcknowledgement
        cause = k.submission.command_id
        if cls is CancellationAcknowledgement and k.pending_cancellation is not None:
            cause = k.pending_cancellation.request_id
        return adapter._account.process_order(adapter._kernel, cls(command_id=item.command_id,
            causation_id=cause, sequence=item.sequence, timestamp=item.timestamp, order_id=k.order_id))

    def _oco_command(self, account, before, item):
        if not isinstance(self.config, AdvancedReplayConfig) or before.state is not S.ACTIVE:
            raise PaperInputError("OCO requires an active advanced session")
        if item.action == "submit_oco":
            if (before.adapter._kernel._view.state is not OrderState.FILLED or before.feed.reason != "fresh"
                    or item.strategy_id != self.config.strategy.strategy_id
                    or before.exit_kernel is not None and not before.exit_kernel._view.terminated):
                raise PaperInputError("OCO requires committed strategy position and exclusive fresh ownership")
            event = account.create_protective_oco(item, risk=self.config.strategy.risk,
                costs=self.config.strategy.costs, liquidity_per_observation=self.config.liquidity_per_observation,
                maximum_age=self.config.stale.maximum_age)
            return event, "oco_submitted"
        return account.process_oco(item.group_id, item), "oco_cancellation"

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

    @staticmethod
    def _rollback_account(account, count):
        for name, key in reversed(account._cache_undo):
            index = getattr(account, name)
            if isinstance(index, set):
                index.discard(key)
            else:
                index.pop(key, None)
        del account._journal[count:]
        account._cache_undo.clear()

    def process(self, item):
        if self._busy:
            raise PaperInputError("session processing already in progress")
        self._busy = True
        try:
            return self._process(item)
        finally:
            self._busy = False

    def _process(self, item):
        if type(item) not in (ReplayEvent, SessionCommand, AdvancedSessionCommand, OCOCommand, EntryCancellationCommand):
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
        if isinstance(item, (AdvancedSessionCommand, OCOCommand)) and not isinstance(self.config, AdvancedReplayConfig):
            raise PaperInputError("v1 session forbids advanced commands")
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
            exit_kernel = None if before.exit_kernel is None else account._kernels[before.exit_kernel._config.session_id][0]
            oco_events = ()
            if type(item) is EntryCancellationCommand:
                orders = self._entry_cancel(adapter, before, item)
                reason = "entry_cancellation"
            elif type(item) is OCOCommand:
                event, reason = self._oco_command(account, before, item)
                oco_events = (event,)
                orders = event.orders
            elif isinstance(item, AdvancedSessionCommand):
                exit_kernel, orders, reason = self._exit_command(account, before, item)
            elif type(item) is SessionCommand:
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
                    k = adapter._kernel._view
                    if adapter._advanced and k.state in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED):
                        pending = ()
                        if k.pending_cancellation is None:
                            pending = account.process_order(adapter._kernel, CancellationRequest(
                                command_id=stable_id("paper-session-entry-stop-request-v3", item),
                                causation_id=k.submission.command_id, order_id=k.order_id,
                                sequence=max(k.last_sequence + 1, item.sequence), timestamp=item.timestamp))
                            k = adapter._kernel._view
                        orders = (*pending, *account.process_order(adapter._kernel, CancellationAcknowledgement(
                            command_id=stable_id("paper-session-entry-stop-ack-v3", item),
                            causation_id=k.pending_cancellation.request_id, order_id=k.order_id,
                            sequence=max(k.last_sequence + 1, item.sequence), timestamp=item.timestamp)))
                    elif k.state is OrderState.ACCEPTED:
                        orders = account.process_order(adapter._kernel, CancellationRequest(
                            command_id=stable_id("paper-session-stop-cancel-v1", item),
                            causation_id=k.submission.command_id, order_id=k.order_id,
                            sequence=max(k.last_sequence + 1, item.sequence), timestamp=item.timestamp))
                    if exit_kernel is not None and not exit_kernel._view.terminated:
                        kexit = exit_kernel._view
                        pending = ()
                        # Preserve existing v2 stop wires below capacity. At the
                        # bound, an existing request permits only its acknowledgement.
                        if kexit.pending_cancellation is None or len(kexit.inputs) < kexit.config.maximum_inputs:
                            cancel = CancellationRequest(command_id=stable_id("paper-session-exit-stop-request-v2", item),
                                causation_id=kexit.submission.command_id, order_id=kexit.order_id,
                                sequence=max(kexit.last_sequence + 1, item.sequence), timestamp=item.timestamp)
                            pending = exit_kernel.process(cancel)
                            kexit = exit_kernel._view
                        ack = CancellationAcknowledgement(command_id=stable_id("paper-session-exit-stop-ack-v2", item),
                            causation_id=kexit.pending_cancellation.request_id, order_id=kexit.order_id,
                            sequence=kexit.last_sequence + 1, timestamp=item.timestamp)
                        orders = (*orders, *pending, *exit_kernel.process(ack))
                    group = account.oco_progress
                    if group is not None and group.active:
                        common = dict(timestamp=item.timestamp, account_id=group.account_id,
                            strategy_id=group.strategy_id, instrument_id=group.instrument_id,
                            position_id=group.position_id, group_id=group.group_id, reason_reference=item.reason_reference)
                        sequence = max(group.last_sequence + 1, item.sequence)
                        if group.pending_request_id is None:
                            request = OCOCommand(action="request_cancel_oco",
                                command_id=stable_id("paper-session-oco-stop-request-v3", item),
                                sequence=sequence, **common)
                            requested = account.process_oco(group.group_id, request)
                            oco_events = (requested,)
                            orders = (*orders, *requested.orders)
                            sequence += 1
                        ack = OCOCommand(action="ack_cancel_oco",
                            command_id=stable_id("paper-session-oco-stop-ack-v3", item),
                            sequence=sequence, **common)
                        acknowledged = account.process_oco(group.group_id, ack)
                        oco_events = (*oco_events, acknowledged)
                        orders = (*orders, *acknowledged.orders)
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
                if account.oco_progress is not None and account.oco_progress.active:
                    event = account.process_oco(account.oco_progress.group_id, item.observation)
                    oco_events = (event,)
                    orders = event.orders
                    reason = "oco_processed"
                elif exit_kernel is not None and not exit_kernel._view.terminated:
                    orders = exit_kernel.process(item.observation)
                    reason = "exit_processed"
                elif runtime._publication.intent is None:
                    reason = "observed"
                elif adapter._kernel._view.submission is None:
                    orders = adapter.submit(item.observation)
                    reason = "submitted"
                elif adapter._advanced and not adapter._kernel._view.terminated:
                    orders = adapter.process_quote(item.observation)
                    reason = "entry_processed"
                else:
                    reason = "terminal_order" if adapter._kernel._view.terminated else "observed"
            elif item.kind == "opening":
                if runtime._publication.intent is None:
                    raise PaperInputError("opening lacks a causal entry intent")
                orders = adapter.process_open(item.observation)
                reason = "opening_processed"
            else:
                reason = "observed"
            financial = tuple(account._journal[financial_count:account.snapshot.event_sequence])
            outcome = self._prepare_record(session_id=self._config.strategy.session_id,
                config_digest=self._config_digest, input_id=identity,
                input_digest=stable_id("paper-session-input-v1", item),
                sequence=item.sequence, timestamp=item.timestamp, state=state,
                transitions=transitions, reason=reason,
                reason_reference=item.reason_reference if isinstance(item, (SessionCommand, OCOCommand, EntryCancellationCommand)) else None,
                market_event=item if type(item) is ReplayEvent else None,
                command=item if isinstance(item, (SessionCommand, OCOCommand, EntryCancellationCommand)) else None,
                decision=decision, orders=orders, financial=financial, account=account.snapshot,
                entry_intent=runtime._publication.intent if adapter._advanced else None,
                entry_order=adapter._kernel._progress() if adapter._advanced else None,
                feed=feed, previous_record_id=before.record_id, oco=account.oco_progress, oco_events=oco_events,
                **({} if exit_kernel is None else {"exit_order": exit_kernel.snapshot if getattr(self, "_legacy_exit_record", False) else exit_kernel._progress()}))
            candidate = replace(before, state=state, clock=clock, feed=feed, runtime=runtime,
                account=account, adapter=adapter, count=before.count + 1, record_id=outcome.record_id,
                exit_kernel=exit_kernel)
            self._stage_record(identity, wire, outcome)
            self._commit_candidate(item, outcome, candidate)
            self._publish_candidate(candidate)
        except BaseException:
            object.__setattr__(self, "_publication", before)
            del self._records[before.count:]
            self._seen.pop(identity, None)
            self._rollback_runtime(runtime, count)
            self._rollback_account(account, before.account.snapshot.event_sequence)
            raise
        account._cache_undo = None
        return outcome

    def replay(self, events, *, maximum_events):
        """Consume at most the explicit bound, in supplied order, without lookahead.

        A batch is not a transaction: each preceding accepted event stays committed.
        Exhaustion is recorded explicitly by the producer, never inferred from EOF.
        """
        if type(maximum_events) is not int or not 0 <= maximum_events <= self._config.maximum_inputs:
            raise PaperInputError("invalid replay processing bound")
        return tuple(self.process(event) for event in islice(events, maximum_events))
