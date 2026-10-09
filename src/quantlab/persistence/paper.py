"""Durable session boundary; deterministic Python owners remain financial authority."""
from dataclasses import replace
from itertools import islice

from quantlab.paper.errors import PaperInputError
from quantlab.paper.models import CancellationRequest, OrderState, KernelSnapshot, KernelProgress, stable_id
from quantlab.paper.feed import advance_clock, accept_delivery, feed_reason
from quantlab.paper.sessions import PaperSession
from quantlab.paper.session_models import ClockState, SessionCommand, SessionState as S, AdvancedReplayConfig, AdvancedEntryReplayConfig
from quantlab.paper.strategy_models import record
from .contracts import (Checkpoint, Effects, PersistenceError, RecoveryRequired, StoragePolicy)
from .store import Manifest, SQLitePaperStore, _TransactionState
from .contracts import decode
from quantlab.paper.account_models import ReserveFunds, ReleaseFunds, ApplyFill, AdvancedApplyFill, OCOApplyFill, MarkAccount


def _entry_state(publication):
    adapter = publication.adapter
    return adapter._kernel._progress() if adapter._advanced else adapter.snapshot


def _effects(publication, outcome):
    account = publication.account
    kinds = {"reserve": ReserveFunds, "release": ReleaseFunds, "settle": ApplyFill, "settle_advanced": AdvancedApplyFill, "settle_oco": OCOApplyFill, "mark": MarkAccount}
    financial = tuple(decode(kinds[e.kind], account.get_input_record(e.input_id)) for e in outcome.financial)
    return Effects(kernel=_entry_state(publication), intent=publication.runtime._publication.intent,
                   financial_inputs=financial, oco=outcome.oco,
                   **({} if publication.exit_kernel is None else {"exit_kernel": outcome.exit_order}))


def _checkpoint(manifest, entry, publication, outcome):
    p = publication
    r = p.runtime._publication
    body = dict(schema_version=1, session_id=manifest.config.strategy.session_id,
        binding_digest=manifest.binding_digest, ordinal=entry.ordinal, head_digest=entry.digest,
        record_id=outcome.record_id, state=p.state, clock=p.clock, feed=p.feed,
        account=p.account.snapshot, kernel=_entry_state(p), intent=r.intent,
        runtime_sequence=r.last_sequence, runtime_timestamp=r.timestamp,
        runtime_count=r.count, financial_count=p.account.snapshot.event_sequence)
    if p.exit_kernel is not None:
        body["exit_kernel"] = outcome.exit_order
    if outcome.oco is not None:
        body["oco"] = outcome.oco
    return Checkpoint(**body, digest=stable_id("paper-durable-checkpoint-v1", body))


class DurablePaperSession(PaperSession):
    """Exclusive serialized wrapper; new construction never resumes stored state.

    Use recover() with a newly opened store. Recovered ACTIVE sessions accept
    exact retries and explicit pause/stop/fail commands, requiring a recorded
    pause then resume before any new market processing. Paused/stopped/failed
    states remain exact. Unknown commits poison all public state access.
    """
    def __init__(self, store, config, *, storage_policy=None, strategy, policy, eligibility, evidence, _recover=False):
        if type(store) is not SQLitePaperStore:
            raise PaperInputError("canonical SQLite paper store required")
        super().__init__(config, strategy=strategy, policy=policy, eligibility=eligibility, evidence=evidence)
        self._store = store
        self._poisoned = False
        self._recovering = _recover
        self._operator_required = False
        storage_policy = StoragePolicy() if storage_policy is None else StoragePolicy.model_validate(storage_policy)
        owner_digest = stable_id("paper-durable-owners-v1", (strategy, policy, eligibility,
            tuple(sorted(evidence._records))))
        admission = self._publication.runtime.admission.record_id
        body = dict(schema_version=1, engine_version="paper-18f-entries-v3" if type(self.config) is AdvancedEntryReplayConfig else (
            "paper-18f-exits-v2" if isinstance(self.config, AdvancedReplayConfig) else "paper-18e-v1"), config=self.config,
            policy=storage_policy, owner_digest=owner_digest, admission_digest=admission)
        self._manifest = Manifest(**body, binding_digest=stable_id("paper-durable-binding-v1", body))
        store._bind(self, self._manifest, recover=_recover)
        self.recovery_checkpoint_ordinal = 0
        self.recovery_replayed_inputs = 0

    @property
    def recovery_required(self):
        return self._poisoned or self._store._transaction_state is not _TransactionState.IDLE

    @property
    def operator_required(self):
        return self._operator_required

    def _readable(self):
        if self.recovery_required:
            raise RecoveryRequired("memory_requires_recovery")

    @property
    def snapshot(self):
        self._readable()
        return super().snapshot

    @property
    def records(self):
        self._readable()
        return super().records

    def _process(self, item):
        self._readable()
        self._store._check()
        before_count = self._publication.count
        if self._operator_required:
            identity = getattr(item, "command_id", getattr(item, "event_id", None))
            if identity not in self._seen and not (type(item) is SessionCommand and item.action in ("pause", "stop", "fail")):
                raise RecoveryRequired("operator_pause_required")
        try:
            outcome = super()._process(item)
            # A historical result never changes the current operator gate.
            if (self._operator_required and self._publication.count > before_count
                    and type(item) is SessionCommand and item.action in ("pause", "stop", "fail")
                    and self._publication.state is not S.ACTIVE):
                self._operator_required = False
            if self._store._transaction_state is _TransactionState.COMMITTED:
                self._store._publication_complete(self)
            return outcome
        except BaseException as exc:
            state = self._store._transaction_state
            if state is not _TransactionState.IDLE or isinstance(exc, RecoveryRequired):
                self._poisoned = True
            if state is _TransactionState.COMMITTED:
                raise RecoveryRequired("committed_unpublished") from exc
            raise

    def _commit_candidate(self, item, outcome, candidate):
        if self._recovering:
            return
        effects = _effects(candidate, outcome)
        interval = self._manifest.policy.checkpoint_interval
        prepare = None
        if interval and candidate.count % interval == 0:
            prepare = lambda entry: _checkpoint(self._manifest, entry, candidate, outcome)
        self._store._append(item, outcome, effects, expected_count=candidate.count - 1, checkpoint=prepare)

    def checkpoint(self):
        """Create a bounded accelerator at the current durable head, on demand."""
        self._readable()
        self._store._check()
        if self._busy:
            raise PaperInputError("session processing already in progress")
        if self._publication.count == 0:
            raise PaperInputError("checkpoint requires a committed input")
        self._busy = True
        try:
            out = self._records[self._publication.count - 1]
            entry = self._store.lookup(out.input_id)
            if entry is None:
                raise RecoveryRequired("durable_head_mismatch")
            cp = _checkpoint(self._manifest, entry, self._publication, out)
            result = self._store._save_checkpoint(cp)
            if self._store._transaction_state is _TransactionState.COMMITTED:
                self._store._publication_complete(self)
            return result
        except BaseException as exc:
            if self._store._transaction_state is not _TransactionState.IDLE or isinstance(exc, RecoveryRequired):
                self._poisoned = True
            raise
        finally:
            self._busy = False

    @classmethod
    def recover(cls, store, config, *, storage_policy=None, use_checkpoint=True, **owners):
        """Verify before activation, restore indexed prefix, replay verified suffix."""
        if type(use_checkpoint) is not bool:
            raise PaperInputError("boolean checkpoint selection required")
        owner = None
        try:
            owner = cls(store, config, storage_policy=storage_policy, _recover=True, **owners)
            retained = store.verify()
            checkpoints = store.checkpoints()
            for cp in checkpoints:
                owner._verify_checkpoint(cp, retained)
            if use_checkpoint and checkpoints:
                cp = checkpoints[-1]
                owner._restore_checkpoint(cp, retained[:cp.ordinal])
                owner.recovery_checkpoint_ordinal = cp.ordinal
            for entry, item, expected, effects in retained[owner.recovery_checkpoint_ordinal:]:
                actual = owner._replay_item(item, expected)
                if actual != expected or _effects(owner._publication, actual) != effects:
                    raise PersistenceError("replay_mismatch")
                owner.recovery_replayed_inputs += 1
            owner._recovering = False
            owner._operator_required = owner._publication.state is S.ACTIVE
            store.last_recovery_failure = None
            return owner
        except BaseException as exc:
            if owner is not None:
                owner._poisoned = True
                store._owner = None
            reason = exc.reason if isinstance(exc, PersistenceError) else "replay_failed"
            store.last_recovery_failure = reason
            if isinstance(exc, PersistenceError):
                raise
            if isinstance(exc, Exception):
                raise PersistenceError(reason) from exc
            raise

    def _replay_item(self, item, expected):
        # Existing v2 full-history wires retain their IDs; new writes use heads.
        self._legacy_exit_record = isinstance(expected.exit_order, KernelSnapshot)
        try:
            return self.process(item)
        finally:
            self._legacy_exit_record = False

    def _verify_checkpoint(self, cp, retained):
        if cp.binding_digest != self._manifest.binding_digest or cp.session_id != self.config.strategy.session_id or not 1 <= cp.ordinal <= len(retained):
            raise PersistenceError("checkpoint_reference")
        entry, item, out, effects = retained[cp.ordinal - 1]
        prefix = retained[:cp.ordinal]
        decisions = [r for _, _, r, _ in prefix if r.decision is not None]
        last = decisions[-1].decision if decisions else None
        if (cp.head_digest != entry.digest or cp.record_id != out.record_id
                or cp.state != out.state or cp.clock != ClockState(sequence=item.sequence, timestamp=item.timestamp)
                or cp.feed != out.feed or cp.account != out.account or cp.kernel != effects.kernel
                or cp.exit_kernel != effects.exit_kernel or cp.exit_kernel != out.exit_order
                or cp.oco != effects.oco or cp.oco != out.oco
                or cp.intent != effects.intent or cp.runtime_count != len(decisions)
                or cp.financial_count != sum(len(r.financial) for _, _, r, _ in prefix)
                or cp.runtime_sequence != (last.sequence if last else 0)
                or cp.runtime_timestamp != (last.timestamp if last else self.config.strategy.timestamp)):
            raise PersistenceError("checkpoint_state")

    def _restore_checkpoint(self, cp, retained):
        """Rebuild private owners and verify ALL prefix operational semantics.

        Use the original runtime/account/adapter engines, including causal opening
        acknowledgement publication. Digests alone do not establish semantics.
        No prefix SessionRecord is regenerated or durably written.
        """
        if isinstance(self.config, AdvancedReplayConfig):
            # V2 keeps full operational replay, including exit ownership and
            # acknowledgements. A checkpoint is verified, not a trust shortcut.
            for _, item, out, effects in retained:
                actual = self._replay_item(item, out)
                if actual != out or _effects(self._publication, actual) != effects:
                    raise PersistenceError("checkpoint_operation_mismatch")
            if (self._publication.account.oco_progress != cp.oco or
                    self._publication.account.snapshot != cp.account or
                    _entry_state(self._publication) != cp.kernel or
                    (None if self._publication.exit_kernel is None else
                     self._publication.exit_kernel._progress() if isinstance(cp.exit_kernel, KernelProgress) else
                     self._publication.exit_kernel.snapshot) != cp.exit_kernel):
                raise PersistenceError("checkpoint_state")
            return
        p = self._publication
        account, runtime, adapter = p.account, p.runtime, p.adapter
        if cp.kernel.config != adapter._kernel._config:
            raise PersistenceError("configuration_mismatch")
        clock, feed, state = p.clock, p.feed, p.state
        for _, item, out, effects in retained:
            terminal = type(item) is SessionCommand and item.action in ("stop", "fail")
            if len(self._records) >= self.config.maximum_inputs and not terminal:
                raise PersistenceError("checkpoint_capacity")
            clock = advance_clock(clock, item)
            if state in (S.STOPPED, S.FAILED):
                raise PersistenceError("checkpoint_lifecycle")
            transitions, decision, orders = (), None, ()
            financial_count = account.snapshot.event_sequence
            if type(item) is SessionCommand:
                feed = feed.model_copy(update={"reason": feed_reason(feed, clock.timestamp, self.config.stale)})
                legal = {"start": (S.CREATED, S.ACTIVE, "started"),
                         "pause": (S.ACTIVE, S.PAUSED, "paused"),
                         "resume": (S.PAUSED, S.ACTIVE, "resumed")}
                if item.action in legal:
                    required, target, reason = legal[item.action]
                    if state is not required:
                        raise PersistenceError("checkpoint_lifecycle")
                    state, transitions = target, (target,)
                else:
                    if item.action == "fail" and state is S.CREATED:
                        raise PersistenceError("checkpoint_lifecycle")
                    k = adapter.snapshot
                    if k.state is OrderState.ACCEPTED:
                        orders = account.process_order(adapter._kernel, CancellationRequest(
                            command_id=stable_id("paper-session-stop-cancel-v1", item),
                            causation_id=k.submission.command_id, order_id=k.order_id,
                            sequence=max(k.last_sequence + 1, item.sequence), timestamp=item.timestamp))
                    if account.snapshot.reservations:
                        raise PersistenceError("checkpoint_reservations")
                    state = S.FAILED if item.action == "fail" else S.STOPPED
                    reason = "failed" if item.action == "fail" else "stopped"
                    transitions = (S.STOPPING, state)
            else:
                if state is S.CREATED or item.provenance not in self.config.sources:
                    raise PersistenceError("configuration_mismatch")
                if item.observation is not None:
                    obs = item.observation
                    market = obs.bar if item.kind == "bar_close" else obs.quote
                    if market.instrument_id != self.config.strategy.account.instrument.instrument_id:
                        raise PersistenceError("configuration_mismatch")
                feed = accept_delivery(feed, item, self.config.stale)
                if state is S.PAUSED:
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
                    # The adapter checks intent, timing, adjacency and identity;
                    # the account atomically generates the ACK with settlement.
                    orders = adapter.process_open(item.observation)
                    reason = "opening_processed"
                else:
                    reason = "observed"
            financial = tuple(islice(account._journal, financial_count, account.snapshot.event_sequence))
            reference = item.reason_reference if type(item) is SessionCommand else None
            if (out.state != state or out.feed != feed or out.transitions != transitions
                    or out.reason != reason or out.reason_reference != reference
                    or out.decision != decision or out.orders != orders
                    or out.financial != financial or out.account != account.snapshot
                    or effects != _effects(p, out)):
                raise PersistenceError("checkpoint_operation_mismatch")
            self._records.append(out)
            self._seen[out.input_id] = (item.canonical_json(), out)
        if (account.snapshot != cp.account or adapter.snapshot != cp.kernel
                or runtime._publication.intent != cp.intent or state != cp.state
                or clock != cp.clock or feed != cp.feed):
            raise PersistenceError("checkpoint_state")
        # Snapshot construction performs the existing typed runtime binding checks.
        runtime.snapshot
        self._publication = replace(p, state=state, clock=clock, feed=feed,
            count=cp.ordinal, record_id=cp.record_id)
