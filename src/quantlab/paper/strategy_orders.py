"""Trusted strategy-to-owned-order adapter; no caller risk/fill/decision injection."""
from .accounts import PaperAccount
from .errors import PaperIdentityConflict, PaperInputError
from .models import MarketDelivery, OrderSubmission, stable_id
from .runtime import StrategyRuntime
from .strategy_models import OpeningDelivery, StrategyOrderSnapshot


class StrategyOrderAdapter:
    """One admitted runtime and one account-owned entry kernel.

    Explicit on-time close and next-open observations are supplied by a trusted
    offline producer. Arbitrary subsequent quotes cannot reach this matching path.
    Admission/intent/strategy records cannot be submitted as execution commands.
    """
    def __init__(self, runtime: StrategyRuntime, account: PaperAccount):
        if type(runtime) is not StrategyRuntime or type(account) is not PaperAccount:
            raise PaperInputError("adapter requires trusted runtime and account owners")
        cfg, snapshot = runtime.config, account.snapshot
        if (not runtime.admission.admitted or snapshot.config != cfg.account
                or snapshot.position is not None or snapshot.reservations
                or snapshot.timestamp > cfg.timestamp or account._kernels):
            raise PaperInputError("adapter requires a flat exclusive compatible account")
        self._runtime, self._account = runtime, account
        self._kernel = account.create_order_kernel(session_id=cfg.session_id,
            strategy_id=cfg.strategy_id, risk=cfg.risk, costs=cfg.costs)

    @property
    def snapshot(self):
        return self._kernel.snapshot

    def _require_flat_owner(self, *, reserved):
        account, kernel = self._account.snapshot, self._kernel.snapshot
        if (account.position is not None or account.equity != kernel.config.flat_equity
                or account.running_peak_equity != kernel.config.running_peak_equity
                or any(r.order_id != kernel.order_id for r in account.reservations)
                or len(account.reservations) != (1 if reserved else 0)):
            raise PaperInputError("strategy account state changed outside its exclusive entry")

    @property
    def audit_snapshot(self):
        intent = self._runtime._publication.intent
        decision = None if intent is None else self._runtime._seen[intent.causation_id][1]
        return StrategyOrderSnapshot(admission=self._runtime.admission, decision=decision,
            intent=intent, kernel=self._kernel.snapshot, openings=self.openings)

    def submit(self, close: MarketDelivery):
        """Convert only the runtime's retained intent, retaining its full identity.

        The command ID hashes the exact strategy version/digest, decision, account
        and admission. Kernel causation binds to the actual delivered close quote.
        No caller-supplied RiskDecision, quantity, side or intent is accepted.
        """
        intent = self._runtime.retained_intent()
        if type(close) is not MarketDelivery:
            raise PaperInputError("entry requires an explicitly delivered close quote")
        try:
            close = MarketDelivery.model_validate(close)
            close.canonical_json()
        except (ValueError, TypeError) as exc:
            if type(close.event_id) is str and close.event_id in self._kernel._current_state().seen:
                raise PaperIdentityConflict("retained close identity reused with invalid content") from exc
            raise PaperInputError("invalid close quote delivery") from exc
        if close.event_id in self._kernel._current_state().seen:
            self._kernel._prepare(close)  # Validate identity replay before timing checks.
        if (close.quote.instrument_id != intent.instrument_id
                or close.timestamp != intent.source_bar_end
                or close.quote.timestamp != intent.source_bar_end
                or close.delivered_at != intent.source_bar_end
                or close.quote.available_at != intent.source_bar_end):
            raise PaperInputError("entry requires an on-time actual close quote")
        command = OrderSubmission(command_id=stable_id("paper-strategy-submission-v1", intent),
            causation_id=close.event_id, sequence=close.sequence + 1,
            timestamp=intent.source_bar_end, instrument_id=intent.instrument_id,
            side=intent.side, quantity=intent.quantity)
        if self._kernel.snapshot.submission is None:
            if self._runtime._publication.history[-1].event_id != intent.causation_id:
                raise PaperInputError("a missed close cannot submit a retrospective entry")
            self._require_flat_owner(reserved=False)
        return self._account._submit_strategy_entry(self._kernel, close, command)

    def process_open(self, opening: OpeningDelivery):
        """Match only a proven explicit next opening; account owns risk/fill/settlement."""
        intent = self._runtime.retained_intent()
        if type(opening) is not OpeningDelivery:
            raise PaperInputError("matching requires an explicit next-opening delivery")
        try:
            opening = OpeningDelivery.model_validate(opening)
            wire = opening.canonical_json()
        except (ValueError, TypeError) as exc:
            if (type(opening.event_id) is str and
                    self._account._opening_acknowledgement(self._kernel, opening.event_id) is not None):
                raise PaperIdentityConflict("retained opening identity reused with invalid content") from exc
            raise PaperInputError("invalid next-opening delivery") from exc
        prior = self._account._opening_acknowledgement(self._kernel, opening.event_id)
        if prior is not None:
            if wire != prior.wire:
                raise PaperIdentityConflict("opening identity reused with different content")
            return prior.records
        if opening.event_id in self._kernel._current_state().seen:
            raise PaperIdentityConflict("opening identity collides with a retained order input")
        if self._kernel.snapshot.terminated:
            raise PaperInputError("single entry order already terminated")
        if (self._kernel.snapshot.submission is None
                or opening.previous_close_id != intent.causation_id
                or opening.bar_start != intent.source_bar_end
                or opening.timeframe is not self._runtime.config.timeframe
                or opening.quote.instrument_id != intent.instrument_id):
            raise PaperInputError("opening does not causally follow the retained entry decision")
        # If another completed bar has arrived, this is a late execution attempt.
        # Exact retries of account-retained openings still replay after later input.
        if self._runtime._publication.history[-1].event_id != intent.causation_id:
            raise PaperInputError("a missed next opening cannot be executed retrospectively")
        self._require_flat_owner(reserved=True)
        # Retain classification, adjacency and interval in the kernel delivery ID,
        # so changing any opening metadata conflicts rather than replaying a fill.
        return self._account._process_strategy_open(self._kernel, opening)

    @property
    def openings(self) -> tuple[OpeningDelivery, ...]:
        return self._account._opening_records(self._kernel)
