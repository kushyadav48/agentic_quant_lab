"""Frozen v1 replay inputs and session audit contracts; no execution authority."""
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from quantlab.data.models import UtcTimestamp
from quantlab.strategies.schema import Digest
from .account_models import AccountEvent, AccountSnapshot
from .models import Identity, LogicalInput, MarketDelivery, PaperContract, PaperEvent
from .strategy_models import (BarCloseDelivery, ContentRecord, OpeningDelivery,
    RuntimeSnapshot, StrategyDecision, StrategyOrderSnapshot, StrategySessionConfig)


class SessionState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class FeedProvenance(PaperContract):
    dataset_id: Identity
    version_digest: Digest
    source_id: Identity
    source_reference: Identity
    classification: Literal["historical", "synthetic-declared-openings"] = "historical"


class StaleFeedPolicy(PaperContract):
    schema_version: Literal[1] = 1
    policy_id: Identity
    version: Annotated[int, Field(ge=1)]
    maximum_age: timedelta

    @model_validator(mode="after")
    def nonnegative(self) -> Self:
        if self.maximum_age < timedelta(0):
            raise ValueError("stale threshold must be nonnegative")
        return self


class ReplayConfig(PaperContract):
    schema_version: Literal[1] = 1
    version: Annotated[int, Field(ge=1)] = 1
    strategy: StrategySessionConfig
    stale: StaleFeedPolicy
    sources: tuple[FeedProvenance, ...] = Field(min_length=1, max_length=32)
    maximum_inputs: Annotated[int, Field(ge=1, le=100_000)] = 20_000  # Plus one terminal record.
    stop_policy: Literal["cancel-pending-non-liquidating-v1"] = "cancel-pending-non-liquidating-v1"

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("duplicate provenance")
        return self


class ReplayEvent(LogicalInput):
    schema_version: Literal[1] = 1
    event_id: Identity
    kind: Literal["bar_close", "quote", "opening", "heartbeat", "interruption", "resumption", "exhaustion"]
    provenance: FeedProvenance
    occurred_at: UtcTimestamp
    available_at: UtcTimestamp
    delivered_at: UtcTimestamp
    observation: BarCloseDelivery | MarketDelivery | OpeningDelivery | None = None

    @model_validator(mode="after")
    def envelope(self) -> Self:
        if not self.occurred_at <= self.available_at <= self.delivered_at <= self.timestamp:
            raise ValueError("occurrence <= availability <= delivery <= effective time required")
        expected = {"bar_close": BarCloseDelivery, "quote": MarketDelivery, "opening": OpeningDelivery}
        cls = expected.get(self.kind)
        if cls is None:
            if self.observation is not None:
                raise ValueError("control event cannot carry market data")
            return self
        if type(self.observation) is not cls:
            raise ValueError("event kind requires its canonical observation")
        obs = self.observation
        market = obs.bar if cls is BarCloseDelivery else obs.quote
        occurrence = market.end_time if cls is BarCloseDelivery else market.timestamp
        if ((obs.event_id, obs.sequence, obs.timestamp, obs.delivered_at) !=
                (self.event_id, self.sequence, self.timestamp, self.delivered_at)
                or (occurrence, market.available_at) != (self.occurred_at, self.available_at)
                or (market.dataset_id, market.source_id) !=
                (self.provenance.dataset_id, self.provenance.source_id)):
            raise ValueError("envelope must preserve payload identity, timing and provenance")
        if cls is OpeningDelivery and self.provenance.classification != "synthetic-declared-openings":
            raise ValueError("historical observations do not prove an opening")
        return self


class SessionCommand(LogicalInput):
    schema_version: Literal[1] = 1
    command_id: Identity
    action: Literal["start", "pause", "resume", "stop", "fail"]
    reason_reference: Identity


class ClockState(PaperContract):
    sequence: Annotated[int, Field(ge=0)] = 0
    timestamp: UtcTimestamp


FeedReason = Literal["fresh", "missing", "stale", "interrupted", "exhausted"]


class FeedState(PaperContract):
    status: Literal["ready", "interrupted", "exhausted"] = "ready"
    last_event_id: Identity | None = None
    last_delivery: UtcTimestamp | None = None
    last_market_at: UtcTimestamp | None = None
    reason: FeedReason = "missing"
    accepted: Annotated[int, Field(ge=0)] = 0


SessionReason = Literal["started", "paused", "resumed", "stopped", "failed",
    "evaluated", "submitted", "opening_processed", "observed", "inactive",
    "missing", "stale", "interrupted", "exhausted", "no_entry", "terminal_order"]


class SessionRecord(ContentRecord):
    namespace = "paper-session-record-v1"
    session_id: Identity
    config_digest: Digest
    input_id: Identity
    input_digest: Digest
    sequence: Annotated[int, Field(gt=0)]
    timestamp: UtcTimestamp
    state: SessionState
    transitions: tuple[SessionState, ...] = ()
    reason: SessionReason
    reason_reference: Identity | None = None
    market_event: ReplayEvent | None = None
    command: SessionCommand | None = None
    decision: StrategyDecision | None = None
    orders: tuple[PaperEvent, ...] = ()
    financial: tuple[AccountEvent, ...] = ()
    account: AccountSnapshot
    feed: FeedState
    previous_record_id: Digest | None


class SessionSnapshot(PaperContract):
    config: ReplayConfig
    state: SessionState
    clock: ClockState
    feed: FeedState
    account: AccountSnapshot
    runtime: RuntimeSnapshot
    execution: StrategyOrderSnapshot
    records: tuple[SessionRecord, ...]
