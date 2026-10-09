"""Serialized in-memory coordination; paper owners remain independent."""
from dataclasses import dataclass
from decimal import DecimalException, localcontext

from quantlab.paper.account_models import ZERO, exact_context
from quantlab.paper.accounting import initialize_account
from quantlab.paper.history import History, RetainedMap
from quantlab.paper.models import stable_id
from quantlab.paper.strategy_models import record
from .errors import PortfolioBudgetError, PortfolioError, PortfolioIdentityConflict
from .models import (EnrollMember, MemberView, ObserveSession, PortfolioConfig,
    PortfolioEvent, PortfolioSnapshot, RefreshPortfolio, ValueMember, aggregate, member_metrics)


@dataclass(frozen=True)
class _Publication:
    snapshot: PortfolioSnapshot
    history: History
    seen: RetainedMap


class Portfolio:
    """One caller serializes local operations. No account writes or I/O.

    Budgets gate static membership admission. Observations report committed
    financial facts, including breaches; they are never trade permission.
    """
    def __init__(self, config: PortfolioConfig):
        try:
            config = PortfolioConfig.model_validate(config)
            self._publication = _Publication(PortfolioSnapshot(config=config,
                timestamp=config.timestamp, **aggregate(config, ())), History(), RetainedMap())
        except (ValueError, TypeError, DecimalException) as exc:
            raise PortfolioError("invalid portfolio configuration") from exc
        self._busy = False

    @property
    def snapshot(self):
        return self._publication.snapshot

    @property
    def events(self):
        """Consumer-requested history materialization only."""
        return tuple(self._publication.history)

    def _publish(self, candidate):
        self._publication = candidate

    def process(self, operation):
        if self._busy:
            raise PortfolioError("reentrant portfolio operation")
        if type(operation) not in (EnrollMember, ObserveSession, RefreshPortfolio, ValueMember):
            raise PortfolioError("expected a canonical portfolio operation")
        before = self._publication
        self._busy = True
        try:
            item = type(operation).model_validate(operation)
            wire = item.canonical_json()
            old = before.seen.get(item.operation_id)
            if old is not None:
                if old[0] != wire:
                    raise PortfolioIdentityConflict("operation identity reused with conflicting content")
                return old[1]
            state = before.snapshot
            if (len(before.history) >= state.config.maximum_operations
                    or item.sequence <= state.sequence or item.timestamp < state.timestamp):
                raise PortfolioError("operation capacity or chronology exceeded")
            views = list(state.members)  # Bounded current membership, never history.
            if type(item) is EnrollMember:
                member = item.member
                cfg = member.config.strategy
                if (any(v.member.member_id == member.member_id for v in views)
                        or any(v.member.config.strategy.account.account_id == cfg.account.account_id
                               or v.member.config.strategy.session_id == cfg.session_id for v in views)):
                    raise PortfolioIdentityConflict("member/account/session already owned")
                if (len(views) >= state.config.maximum_members or cfg.timestamp > item.timestamp
                        or cfg.account.denomination != state.config.denomination):
                    raise PortfolioError("incompatible member capacity, currency or activation time")
                with localcontext(exact_context()):
                    if member.capital + state.allocated_capital > state.config.total_capital:
                        raise PortfolioBudgetError("capital allocation exceeds owned total")
                    for name in ("maximum_encumbered", "maximum_gross_exposure"):
                        used = sum((getattr(v.member.risk, name) for v in views), ZERO)
                        if used + getattr(member.risk, name) > getattr(state.config.risk, name):
                            raise PortfolioBudgetError("risk allocation exceeds owned budget")
                views.append(MemberView(member=member, account=initialize_account(cfg.account),
                    session_timestamp=cfg.timestamp, valuation_status="missing", gross_exposure=None))
            elif type(item) in (ObserveSession, ValueMember):
                index = next((i for i, v in enumerate(views) if v.member.member_id == item.member_id), None)
                if index is None:
                    raise PortfolioError("observation requires explicit membership")
                views[index] = self._observe(views[index], item) if type(item) is ObserveSession else self._value(views[index], item)
            views.sort(key=lambda v: v.member.member_id)
            fresh_views = []
            for v in views:
                status, gross, pnl, equity = member_metrics(v.member, v.account, v.session_record_id,
                    v.session_timestamp, item.timestamp, state.config.maximum_valuation_age, v.valuation)
                fresh_views.append(v.model_copy(update={"valuation_status": status, "gross_exposure": gross,
                    "unrealized_pnl": pnl, "equity": equity}))
            views = tuple(fresh_views)
            # Bind the pre-event snapshot without a circular event/snapshot digest.
            snapshot = PortfolioSnapshot(config=state.config, sequence=item.sequence,
                timestamp=item.timestamp, last_event_id=state.last_event_id, members=views,
                **aggregate(state.config, views))
            outcome = record(PortfolioEvent, portfolio_id=state.config.portfolio_id,
                operation=item, previous_event_id=state.last_event_id,
                snapshot_digest=stable_id("portfolio-snapshot-v1", snapshot))
            snapshot = PortfolioSnapshot.model_validate(snapshot.model_copy(update={"last_event_id": outcome.record_id}))
            snapshot.canonical_json()
            candidate = _Publication(snapshot,
                before.history.append((outcome,), (outcome.canonical_json(),)),
                before.seen.set(item.operation_id, (wire, outcome)))
            self._publish(candidate)
            return outcome
        except (ValueError, TypeError, DecimalException) as exc:
            self._publication = before
            if isinstance(exc, PortfolioError):
                raise
            raise PortfolioError("invalid or conflicting portfolio state") from exc
        except BaseException:
            self._publication = before
            raise
        finally:
            self._busy = False

    @staticmethod
    def _value(view, item):
        source = item.source
        position = view.account.position
        if (view.session_record_id is None or position is None or position.quantity == 0
                or source.timestamp > item.timestamp
                or source.quote.timestamp < position.entry_time
                or source.quote.instrument_id != view.member.config.strategy.account.instrument.instrument_id
                or item.provenance not in view.member.config.sources
                or (source.quote.dataset_id, source.quote.source_id) !=
                   (item.provenance.dataset_id, item.provenance.source_id)):
            raise PortfolioError("valuation requires an owned open position and compatible causal source")
        if view.valuation_head is not None and source.sequence <= view.valuation_head.sequence:
            raise PortfolioError("portfolio valuation chronology moved backwards")
        for prior in (view.valuation_head, position.valuation):
            if prior is not None and (source.timestamp < prior.timestamp
                    or source.quote.timestamp < prior.quote.timestamp
                    or source.quote.available_at < prior.quote.available_at or source.delivered_at < prior.delivered_at):
                raise PortfolioError("portfolio valuation chronology moved backwards")
        return view.model_copy(update={"valuation": source, "valuation_head": source})

    @staticmethod
    def _observe(view, item):
        r, cfg, account = item.record, view.member.config.strategy, view.account
        if (r.session_id != cfg.session_id
                or r.config_digest != stable_id("paper-replay-config-v1", view.member.config)
                or r.previous_record_id != view.session_record_id
                or r.sequence <= view.session_sequence or r.timestamp < view.session_timestamp
                or r.timestamp > item.timestamp or r.account.config != cfg.account
                or r.account.timestamp > r.timestamp or r.account.timestamp < account.timestamp):
            raise PortfolioError("session provenance, chain or chronology mismatch")
        if (r.market_event is None) == (r.command is None):
            raise PortfolioError("session record requires exactly one retained input")
        original = r.market_event if r.market_event is not None else r.command
        input_id = original.event_id if r.market_event is not None else original.command_id
        if ((r.input_id, r.sequence, r.timestamp) != (input_id, original.sequence, original.timestamp)
                or r.input_digest != stable_id("paper-session-input-v1", original)):
            raise PortfolioError("session record does not bind its retained input")
        if r.market_event is not None:
            if r.market_event.provenance not in view.member.config.sources:
                raise PortfolioError("incompatible market provenance")
            observation = r.market_event.observation
            if observation is not None:
                market = observation.bar if hasattr(observation, "bar") else observation.quote
                if market.instrument_id != cfg.account.instrument.instrument_id:
                    raise PortfolioError("incompatible market instrument")
        if r.decision is not None and (
                r.decision.admission_id != view.member.admission.record_id
                or r.decision.account_id != cfg.account.account_id or r.decision.session_id != cfg.session_id
                or r.decision.policy_digest != view.member.admission.policy_digest
                or (r.decision.strategy_id, r.decision.strategy_version, r.decision.strategy_digest) !=
                   (cfg.strategy_id, cfg.strategy_version, cfg.strategy_digest)):
            raise PortfolioError("conflicting decision attribution")
        version, last_id = account.state_version, account.last_event_id
        timestamp = account.timestamp
        for event in r.financial:
            if (event.account_id != cfg.account.account_id or event.strategy_id != cfg.strategy_id
                    or event.previous_event_id != last_id or event.before_version != version
                    or not timestamp <= event.timestamp <= r.timestamp):
                raise PortfolioError("financial event chain or attribution mismatch")
            version, last_id, timestamp = event.after_version, event.event_id, event.timestamp
        if not r.financial:
            if r.account != account:
                raise PortfolioError("account changed without a financial event")
        else:
            if (r.account.state_version != version or r.account.last_event_id != last_id
                    or r.account.timestamp != timestamp
                    or r.account.last_input_sequence != r.financial[-1].input_sequence):
                raise PortfolioError("account does not match financial event head")
            for name in ("balance", "equity", "available_funds", "reserved_funds", "position_collateral",
                         "realized_pnl", "unrealized_pnl", "fees_paid"):
                if getattr(r.account, name) != getattr(r.financial[-1], name):
                    raise PortfolioError("account economics conflict with financial event")
        position = r.account.position
        if position is not None:
            source = position.valuation.quote
            if not any((p.dataset_id, p.source_id) == (source.dataset_id, source.source_id)
                       for p in view.member.config.sources):
                raise PortfolioError("incompatible valuation source")
            prior = account.position
            if (prior is not None and prior.quantity > 0 and position.quantity > 0
                    and position.valuation.quote.timestamp < prior.valuation.quote.timestamp):
                raise PortfolioError("valuation chronology moved backwards")
        valuation = view.valuation
        if (position is None or position.quantity == 0 or account.position is None
                or position.entry_transaction_id != account.position.entry_transaction_id
                or (valuation is not None and position.valuation != account.position.valuation
                    and position.valuation.quote.timestamp >= valuation.quote.timestamp)):
            valuation = None
        return MemberView(member=view.member, account=r.account, session_record_id=r.record_id,
            session_sequence=r.sequence, session_timestamp=r.timestamp, session_state=r.state,
            feed=r.feed, valuation=valuation, valuation_head=view.valuation_head,
            valuation_status="missing", gross_exposure=None)

    @classmethod
    def replay(cls, config, events):
        """Verify a caller-retained ordered journal into a fresh owner; no durability."""
        if type(events) is not tuple or len(events) > config.maximum_operations:
            raise PortfolioError("expected bounded immutable event journal")
        owner = cls(config)
        for expected in events:
            expected = PortfolioEvent.model_validate(expected)
            if expected.previous_event_id != owner.snapshot.last_event_id:
                raise PortfolioError("portfolio journal is not an ordered unique chain")
            if owner.process(expected.operation) != expected:
                raise PortfolioError("portfolio replay diverged")
        return owner
