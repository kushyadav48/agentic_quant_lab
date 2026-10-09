"""Immutable portfolio reporting contracts; none grant execution authority."""
from datetime import timedelta
from decimal import localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from quantlab.backtesting import PositionSide
from quantlab.data.models import CurrencyCode, NonNegativeDecimal, PositiveDecimal, UtcTimestamp
from quantlab.paper.account_models import AccountSnapshot, ZERO, exact_context
from quantlab.paper.models import Identity, LogicalInput, MarketDelivery, PaperContract, stable_id
from quantlab.paper.session_models import (
    AdvancedEntryReplayConfig, AdvancedReplayConfig, ReplayConfig, SessionRecord, SessionState, FeedProvenance, FeedState,
)
from quantlab.paper.strategy_models import AdmissionRecord, ContentRecord
from quantlab.strategies import ApprovalState, StrategySpecification
from quantlab.strategies.schema import Digest
from quantlab.risk.models import FiniteDecimal


class RiskBudget(PaperContract):
    """Independent absolute limits in the portfolio denomination; no netting."""
    maximum_encumbered: NonNegativeDecimal
    maximum_gross_exposure: NonNegativeDecimal


class PortfolioConfig(PaperContract):
    schema_version: Literal[1] = 1
    portfolio_id: Identity
    denomination: CurrencyCode
    total_capital: PositiveDecimal
    risk: RiskBudget
    timestamp: UtcTimestamp
    maximum_valuation_age: timedelta
    maximum_members: Annotated[int, Field(ge=1, le=32)] = 32
    maximum_operations: Annotated[int, Field(ge=1, le=100_000)] = 20_000

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if (self.maximum_valuation_age < timedelta(0)
                or self.risk.maximum_encumbered > self.total_capital):
            raise ValueError("invalid valuation age or prefunded risk budget")
        self.canonical_json()
        return self


class PortfolioMember(PaperContract):
    """One exclusive account/session and one exact approved strategy version."""
    member_id: Identity
    config: AdvancedEntryReplayConfig | AdvancedReplayConfig | ReplayConfig
    strategy: StrategySpecification
    admission: AdmissionRecord
    risk: RiskBudget

    @property
    def capital(self):
        return self.config.strategy.account.starting_capital

    @model_validator(mode="after")
    def attribution(self) -> Self:
        cfg, spec, admission = self.config.strategy, self.strategy, self.admission
        expected = (spec.strategy_id, spec.version, spec.content_digest)
        if (spec.state is not ApprovalState.APPROVED or spec.approval is None
                or spec.approval.reviewed_at > cfg.timestamp
                or expected != (cfg.strategy_id, cfg.strategy_version, cfg.strategy_digest)
                or expected != (admission.strategy_id, admission.strategy_version, admission.strategy_digest)
                or not admission.admitted or not admission.evidence
                or admission.policy_digest is None or admission.causation_id is None
                or (admission.account_id, admission.session_id, admission.timestamp) !=
                   (cfg.account.account_id, cfg.session_id, cfg.timestamp)
                or admission.config_digest != stable_id("paper-strategy-config-v1", cfg)
                or self.risk.maximum_encumbered > self.capital):
            raise ValueError("member requires exact approved admission and owned prefunded budget")
        self.canonical_json()
        return self


class EnrollMember(LogicalInput):
    operation_id: Identity
    member: PortfolioMember


class ObserveSession(LogicalInput):
    operation_id: Identity
    member_id: Identity
    record: SessionRecord


class RefreshPortfolio(LogicalInput):
    """Advance the explicit reporting time without inventing market observations."""
    operation_id: Identity


class ValueMember(LogicalInput):
    """Explicit reporting quote; never sends a mark or order to the paper owner."""
    operation_id: Identity
    member_id: Identity
    source: MarketDelivery
    provenance: FeedProvenance


PortfolioOperation = EnrollMember | ObserveSession | RefreshPortfolio | ValueMember
ValuationStatus = Literal["fresh", "missing", "stale"]


class MemberView(PaperContract):
    member: PortfolioMember
    account: AccountSnapshot
    session_record_id: Digest | None = None
    session_sequence: Annotated[int, Field(ge=0)] = 0
    session_timestamp: UtcTimestamp
    session_state: SessionState = SessionState.CREATED
    feed: FeedState | None = None
    valuation: MarketDelivery | None = None
    valuation_head: MarketDelivery | None = None  # Retained even when an account mark supersedes it.
    valuation_status: ValuationStatus
    gross_exposure: NonNegativeDecimal | None
    unrealized_pnl: FiniteDecimal | None = None
    equity: FiniteDecimal | None = None


def member_metrics(member, account, record_id, session_timestamp, as_of, maximum_age, valuation=None):
    """Liquidation exposure and freshness use recorded quote time, never wall time."""
    status = "missing" if record_id is None else "fresh"
    p = account.position
    source = valuation if valuation is not None else (None if p is None else p.valuation)
    if status == "fresh" and as_of - session_timestamp > maximum_age:
        status = "stale"
    if p is not None and p.quantity > 0 and as_of - source.quote.timestamp > maximum_age:
        status = "stale" if record_id is not None else "missing"
    gross, pnl, equity = None, None, None
    if status == "fresh":
        with localcontext(exact_context()):
            mark = ZERO if p is None else (source.quote.bid if p.direction is PositionSide.LONG else source.quote.ask)
            gross = ZERO if p is None else p.quantity * mark
            pnl = ZERO if p is None else p.quantity * (mark - p.entry_basis
                if p.direction is PositionSide.LONG else p.entry_basis - mark)
            equity = account.balance + pnl
    return status, gross, pnl, equity


def aggregate(config, views):
    """Reuse reconciled account economics; collateral/holds are balance partitions."""
    with localcontext(exact_context()):
        total = lambda name: sum((getattr(v.account, name) for v in views), ZERO)
        capital = sum((v.member.capital for v in views), ZERO)
        unallocated = config.total_capital - capital
        fresh = all(v.valuation_status == "fresh" for v in views)
        gross = sum((v.gross_exposure for v in views), ZERO) if fresh else None
        encumbered = total("position_collateral") + total("reserved_funds")
        breaches = []
        for v in views:
            if v.account.position_collateral + v.account.reserved_funds > v.member.risk.maximum_encumbered:
                breaches.append(f"member:{v.member.member_id}:encumbered")
            if v.gross_exposure is not None and v.gross_exposure > v.member.risk.maximum_gross_exposure:
                breaches.append(f"member:{v.member.member_id}:gross_exposure")
        if encumbered > config.risk.maximum_encumbered:
            breaches.append("portfolio:encumbered")
        if gross is not None and gross > config.risk.maximum_gross_exposure:
            breaches.append("portfolio:gross_exposure")
        if not fresh:
            breaches.append("portfolio:valuation_unavailable")
        return dict(allocated_capital=capital, unallocated_capital=unallocated,
            balance=unallocated + total("balance"), available_funds=unallocated + total("available_funds"),
            reserved_funds=total("reserved_funds"), position_collateral=total("position_collateral"),
            realized_pnl=total("realized_pnl"), fees_paid=total("fees_paid"),
            reported_unrealized_pnl=total("unrealized_pnl"), reported_equity=unallocated + total("equity"),
            unrealized_pnl=sum((v.unrealized_pnl for v in views), ZERO) if fresh else None,
            equity=unallocated + sum((v.equity for v in views), ZERO) if fresh else None,
            net_pnl=total("realized_pnl") - total("fees_paid") + sum((v.unrealized_pnl for v in views), ZERO) if fresh else None,
            gross_exposure=gross, encumbered=encumbered, risk_breaches=tuple(breaches))


class PortfolioSnapshot(PaperContract):
    config: PortfolioConfig
    sequence: Annotated[int, Field(ge=0)] = 0
    timestamp: UtcTimestamp
    last_event_id: Digest | None = None
    members: tuple[MemberView, ...] = Field(default=(), max_length=32)
    allocated_capital: NonNegativeDecimal
    unallocated_capital: NonNegativeDecimal
    balance: FiniteDecimal
    available_funds: NonNegativeDecimal
    reserved_funds: NonNegativeDecimal
    position_collateral: NonNegativeDecimal
    realized_pnl: FiniteDecimal
    fees_paid: NonNegativeDecimal
    reported_unrealized_pnl: FiniteDecimal
    reported_equity: FiniteDecimal
    unrealized_pnl: FiniteDecimal | None
    equity: FiniteDecimal | None
    net_pnl: FiniteDecimal | None
    gross_exposure: NonNegativeDecimal | None
    encumbered: NonNegativeDecimal
    risk_breaches: tuple[str, ...]

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        if self.timestamp < self.config.timestamp:
            raise ValueError("portfolio chronology mismatch")
        ids = tuple(v.member.member_id for v in self.members)
        if ids != tuple(sorted(set(ids))) or len(ids) > self.config.maximum_members:
            raise ValueError("portfolio membership ordering/bounds mismatch")
        for name in ("account_id", "session_id"):
            values = [getattr(v.member.config.strategy.account if name == "account_id"
                else v.member.config.strategy, name) for v in self.members]
            if len(values) != len(set(values)):
                raise ValueError("duplicate financial/session ownership")
        identities = {}
        instruments = {}
        with localcontext(exact_context()):
            for name in ("maximum_encumbered", "maximum_gross_exposure"):
                if sum((getattr(v.member.risk, name) for v in self.members), ZERO) > getattr(self.config.risk, name):
                    raise ValueError("allocated risk budget exceeds portfolio limit")
        for v in self.members:
            cfg = v.member.config.strategy
            binding = (cfg.strategy_version, cfg.strategy_digest, v.member.strategy.approval)
            if identities.setdefault(cfg.strategy_id, binding) != binding:
                raise ValueError("conflicting strategy identity")
            instrument = cfg.account.instrument
            if instruments.setdefault(instrument.instrument_id, instrument) != instrument:
                raise ValueError("conflicting instrument metadata")
            if (v.account.config != cfg.account or cfg.account.denomination != self.config.denomination
                    or v.session_timestamp > self.timestamp or v.account.timestamp > v.session_timestamp
                    or cfg.timestamp > v.session_timestamp
                    or any(r.strategy_id != cfg.strategy_id for r in v.account.reservations)
                    or (v.account.position is not None and v.account.position.strategy_id != cfg.strategy_id)):
                raise ValueError("incompatible member account attribution or time")
            if v.valuation is not None and v.valuation != v.valuation_head:
                raise ValueError("active valuation must bind its retained explicit quote head")
            for source in (v.valuation, v.valuation_head):
                if source is not None and (source.timestamp > self.timestamp
                        or source.quote.instrument_id != instrument.instrument_id
                        or not any((p.source_id, p.dataset_id) == (source.quote.source_id, source.quote.dataset_id)
                                   for p in v.member.config.sources)
                        or (v.account.position is not None and source.quote.timestamp < v.account.position.entry_time)):
                    raise ValueError("incompatible or future portfolio valuation")
            if (v.valuation_status, v.gross_exposure, v.unrealized_pnl, v.equity) != member_metrics(v.member, v.account,
                    v.session_record_id, v.session_timestamp, self.timestamp, self.config.maximum_valuation_age, v.valuation):
                raise ValueError("valuation does not bind reporting time")
        for name, value in aggregate(self.config, self.members).items():
            if getattr(self, name) != value:
                raise ValueError(f"portfolio aggregate mismatch: {name}")
        return self


class PortfolioEvent(ContentRecord):
    namespace = "portfolio-event-v1"
    portfolio_id: Identity
    operation: PortfolioOperation
    previous_event_id: Digest | None
    snapshot_digest: Digest
