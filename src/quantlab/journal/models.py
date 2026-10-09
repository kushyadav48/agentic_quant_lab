"""Versioned immutable links and views over retained paper/research artifacts."""
from decimal import localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from quantlab.data.models import NonNegativeDecimal, UtcTimestamp
from quantlab.mcp.operation_models import OperationSnapshot
from quantlab.paper.account_models import AccountSnapshot, exact_context
from quantlab.paper.models import Identity, PaperContract, stable_id
from quantlab.paper.session_models import AdvancedEntryReplayConfig, AdvancedReplayConfig, ReplayConfig, SessionRecord
from quantlab.paper.strategy_models import AdmissionRecord, ContentRecord, ResearchEvidence, StrategyBinding
from quantlab.risk.models import FiniteDecimal
from quantlab.strategies import ApprovalState, StrategySpecification
from quantlab.strategies.schema import Digest
from .research import report_bindings, validate_operation


class JournalSession(PaperContract):
    schema_version: Literal[1] = 1
    config: AdvancedEntryReplayConfig | AdvancedReplayConfig | ReplayConfig
    strategy: StrategySpecification
    admission: AdmissionRecord

    @model_validator(mode="after")
    def attribution(self) -> Self:
        cfg, spec, a = self.config.strategy, self.strategy, self.admission
        binding = (spec.strategy_id, spec.version, spec.content_digest)
        if (spec.state is not ApprovalState.APPROVED or spec.approval is None
                or spec.approval.reviewed_at > cfg.timestamp
                or binding != (cfg.strategy_id, cfg.strategy_version, cfg.strategy_digest)
                or binding != (a.strategy_id, a.strategy_version, a.strategy_digest)
                or not a.admitted or not a.evidence or a.policy_digest is None or a.causation_id is None
                or (a.session_id, a.account_id, a.timestamp) !=
                   (cfg.session_id, cfg.account.account_id, cfg.timestamp)
                or a.config_digest != stable_id("paper-strategy-config-v1", cfg)):
            raise ValueError("journal requires an exact approved and admitted session")
        self.canonical_json()
        return self


class ResearchHistoryRecord(ContentRecord):
    namespace = "journal-research-history-v1"
    origin_namespace: Identity
    run_id: Digest
    revision: Annotated[int, Field(ge=1, le=3)]
    timestamp: UtcTimestamp
    operation: OperationSnapshot | None = None
    evidence: ResearchEvidence | None = None

    @property
    def source_kind(self):
        return "operation" if self.operation is not None else "evidence"

    @property
    def source_id(self):
        return self.operation.operation_id if self.operation is not None else self.evidence.record_id

    @property
    def status(self):
        return self.operation.state.value if self.operation is not None else self.evidence.status

    @property
    def strategies(self) -> tuple[StrategyBinding, ...]:
        if self.operation is not None:
            return tuple(StrategyBinding(strategy_id=s.strategy_id, strategy_version=s.version,
                strategy_digest=s.content_digest) for s in self.operation.strategies)
        return tuple(StrategyBinding(strategy_id=s, strategy_version=v, strategy_digest=d)
            for s,v,d in report_bindings(self.evidence.report))

    @model_validator(mode="after")
    def source_binding(self) -> Self:
        if (self.operation is None) == (self.evidence is None):
            raise ValueError("research history requires exactly one retained source")
        expected_revision = len(self.operation.audit) if self.operation is not None else 1
        expected_time = self.operation.audit[-1].timestamp if self.operation is not None else self.evidence.verified_at
        if (self.revision != expected_revision or self.timestamp != expected_time
                or self.run_id != stable_id("journal-research-run-v1",
                    (self.origin_namespace, self.source_kind, self.source_id))):
            raise ValueError("research identity, revision and time must bind retained source")
        if self.operation is not None:
            validate_operation(self.operation)
            times = tuple(e.timestamp for e in self.operation.audit)
            if any(a > b for a, b in zip(times, times[1:])):
                raise ValueError("research audit time moved backwards")
        else:
            report_bindings(self.evidence.report)
        return self


class ResearchLink(ContentRecord):
    """Explicit application association, never proof of admission eligibility."""
    namespace = "journal-research-link-v1"
    session_id: Identity
    research_record_id: Digest
    admission_id: Digest


class NoteRevision(ContentRecord):
    namespace = "journal-note-v1"
    note_id: Identity
    revision: Annotated[int, Field(ge=1)]
    target_kind: Literal["session", "trade", "research"]
    target_id: Identity
    author: Identity
    timestamp: UtcTimestamp
    text: Annotated[str, Field(max_length=8192)]
    previous_record_id: Digest | None = None


class HistoryCursor(PaperContract):
    schema_version: Literal[1] = 1
    scope_digest: Digest
    timestamp: UtcTimestamp
    owner_id: Identity
    sequence: Annotated[int, Field(ge=1)]
    record_id: Digest


class HistoryQuery(PaperContract):
    session_id: Identity | None = None
    account_id: Identity | None = None
    strategy_id: Identity | None = None
    strategy_version: Annotated[int, Field(ge=1)] | None = None
    strategy_digest: Digest | None = None
    instrument_id: Identity | None = None
    order_id: Digest | None = None
    run_id: Digest | None = None
    start: UtcTimestamp | None = None
    end: UtcTimestamp | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    after: HistoryCursor | None = None

    @model_validator(mode="after")
    def range(self) -> Self:
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("history time range is half-open and nonempty")
        return self


class TradeHistoryRecord(PaperContract):
    """One committed session delta, including decisions without any execution."""
    schema_version: Literal[1] = 1
    session: JournalSession
    source: SessionRecord
    realized_pnl_delta: FiniteDecimal
    fees_delta: NonNegativeDecimal
    net_realized_pnl_delta: FiniteDecimal

    @model_validator(mode="after")
    def coherent(self) -> Self:
        cfg = self.session.config.strategy
        with localcontext(exact_context()):
            if (self.net_realized_pnl_delta != self.realized_pnl_delta-self.fees_delta
                    or self.source.session_id != cfg.session_id
                    or self.source.config_digest != stable_id("paper-replay-config-v1", self.session.config)
                    or self.source.account.config != cfg.account):
                raise ValueError("trade view attribution or net outcome mismatch")
        return self


class TradeHistoryPage(PaperContract):
    records: tuple[TradeHistoryRecord, ...]
    next_cursor: HistoryCursor | None = None


class ResearchHistoryPage(PaperContract):
    records: tuple[ResearchHistoryRecord, ...]
    next_cursor: HistoryCursor | None = None


class SessionSummary(PaperContract):
    schema_version: Literal[1] = 1
    session_id: Identity
    source_record_id: Digest | None
    account: AccountSnapshot
    record_count: Annotated[int, Field(ge=0)]
    fill_count: Annotated[int, Field(ge=0)]
    net_realized_pnl: FiniteDecimal
    lifecycle: Literal["no_execution", "open", "closed"]
    closed_outcome: Literal["profit", "loss", "breakeven"] | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        lifecycle = ("no_execution" if self.fill_count == 0 else "open"
            if self.account.position is not None and self.account.position.quantity > 0 else "closed")
        net = self.account.net_realized_pnl
        outcome = None if lifecycle != "closed" else "profit" if net > 0 else "loss" if net < 0 else "breakeven"
        if (self.lifecycle != lifecycle or self.closed_outcome != outcome or self.net_realized_pnl != net
                or (self.source_record_id is None) != (self.record_count == 0)):
            raise ValueError("summary must bind recorded account economics and lifecycle")
        return self


class JournalEvent(ContentRecord):
    namespace = "journal-event-v1"
    ordinal: Annotated[int, Field(ge=1)]
    previous_record_id: Digest | None
    kind: Literal["session", "trade", "research", "link", "note"]
    payload: str
