"""Frozen v1 replay inputs and session audit contracts; no execution authority."""
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator, model_serializer, TypeAdapter
from quantlab.data.models import UtcTimestamp
from quantlab.strategies.schema import Digest
from .account_models import AccountEvent, AccountSnapshot
from .oco_models import OCOCommand, OCOProgress, OCOEvent, OCOSnapshot
from .models import Identity, LogicalInput, MarketDelivery, PaperContract, PaperEvent, KernelSnapshot, KernelProgress
from quantlab.data.models import PositiveDecimal
from .strategy_models import (BarCloseDelivery, ContentRecord, OpeningDelivery,
    RuntimeSnapshot, StrategyDecision, StrategyOrderSnapshot, StrategySessionConfig, AdvancedStrategySessionConfig,
    AdvancedEntryIntent)


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


class AdvancedReplayConfig(ReplayConfig):
    """V2 permits one position-linked exit at a time after the v1 approved entry."""
    schema_version: Literal[2] = 2
    advanced_policy: Literal["position-linked-exits-v2"] = "position-linked-exits-v2"
    liquidity_per_observation: PositiveDecimal | None = None


class AdvancedEntryReplayConfig(AdvancedReplayConfig):
    """V3 selects separately reviewed v2 entries and existing protective exits/OCO."""
    schema_version: Literal[3] = 3
    advanced_policy: Literal["approved-advanced-entries-v3"] = "approved-advanced-entries-v3"
    strategy: AdvancedStrategySessionConfig

    @model_validator(mode="after")
    def approved_age(self) -> Self:
        if self.stale.maximum_age != self.strategy.entry_policy.maximum_age:
            raise ValueError("feed age must match approved entry policy")
        return self


class EntryCancellationCommand(LogicalInput):
    schema_version: Literal[3] = 3
    command_id: Identity
    action: Literal["request_cancel_entry", "ack_cancel_entry"]
    intent_id: Digest
    reason_reference: Identity


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


class AdvancedSessionCommand(SessionCommand):
    schema_version: Literal[2] = 2
    action: Literal["submit_exit", "request_cancel_exit", "ack_cancel_exit"]
    position_id: Identity
    quantity: PositiveDecimal | None = None
    order_type: Literal["market", "limit", "stop_market", "stop_limit"] = "market"
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    protective_role: Literal["stop_loss", "take_profit"] | None = None

    @model_validator(mode="after")
    def parameters(self) -> Self:
        if self.action == "submit_exit":
            if self.quantity is None:
                raise ValueError("exit requires quantity")
            from .models import AdvancedOrderSubmission, OrderSide
            AdvancedOrderSubmission(sequence=self.sequence, timestamp=self.timestamp,
                command_id=self.command_id, causation_id=self.position_id,
                instrument_id="validation", side=OrderSide.SELL, quantity=self.quantity,
                order_type=self.order_type, time_in_force=self.time_in_force,
                limit_price=self.limit_price, stop_price=self.stop_price,
                reduce_only=True, position_id=self.position_id, protective_role=self.protective_role)
        elif (self.quantity is not None or self.order_type != "market" or self.time_in_force != "gtc"
                or self.limit_price is not None or self.stop_price is not None or self.protective_role is not None):
            raise ValueError("cancellation commands forbid order parameters")
        return self


class SessionCommandCodec:
    @staticmethod
    def model_validate_json(wire):
        return TypeAdapter(EntryCancellationCommand | OCOCommand | AdvancedSessionCommand | SessionCommand).validate_json(wire)


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
    "missing", "stale", "interrupted", "exhausted", "no_entry", "terminal_order",
    "exit_submitted", "exit_processed", "exit_cancellation", "oco_submitted", "oco_processed", "oco_cancellation", "entry_processed", "entry_cancellation"]


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
    command: EntryCancellationCommand | OCOCommand | AdvancedSessionCommand | SessionCommand | None = None
    decision: StrategyDecision | None = None
    orders: tuple[PaperEvent, ...] = ()
    financial: tuple[AccountEvent, ...] = ()
    account: AccountSnapshot
    feed: FeedState
    previous_record_id: Digest | None
    entry_intent: AdvancedEntryIntent | None = None
    entry_order: KernelProgress | None = None
    exit_order: KernelProgress | KernelSnapshot | None = None
    oco: OCOProgress | None = None
    oco_events: tuple[OCOEvent, ...] = Field(default=(), max_length=2)

    @model_serializer(mode="wrap")
    def legacy_wire(self, handler):
        body = handler(self)
        if self.entry_intent is None:
            body.pop("entry_intent", None)
        if self.entry_order is None:
            body.pop("entry_order", None)
        if self.exit_order is None:
            body.pop("exit_order", None)
        if self.oco is None:
            body.pop("oco", None)
        if not self.oco_events:
            body.pop("oco_events", None)
        return body


class SessionSnapshot(PaperContract):
    config: AdvancedEntryReplayConfig | AdvancedReplayConfig | ReplayConfig
    state: SessionState
    clock: ClockState
    feed: FeedState
    account: AccountSnapshot
    runtime: RuntimeSnapshot
    execution: StrategyOrderSnapshot
    records: tuple[SessionRecord, ...]
    exit_order: KernelSnapshot | None = None

    oco: OCOSnapshot | None = None

    @model_serializer(mode="wrap")
    def compatible_wire(self, handler):
        body = handler(self)
        if self.oco is None:
            body.pop("oco", None)
        return body
