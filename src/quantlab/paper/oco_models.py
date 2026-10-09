"""Versioned trusted Python OCO commands and immutable audit contracts."""
from typing import Annotated, Literal, Self
from pydantic import Field, model_validator
from quantlab.data.models import PositiveDecimal, NonNegativeDecimal, UtcTimestamp
from quantlab.strategies.schema import Digest
from .account_models import exact_context
from decimal import localcontext
from .models import (Identity, LogicalInput, PaperContract, OCOKernelProgress,
    KernelSnapshot, PaperEvent, OCOFillRecord, OCOQuantityAdjustment, OrderState,
    OrderActivation, StopTrigger, stable_id)
from .strategy_models import ContentRecord


class OCOCommand(LogicalInput):
    schema_version: Literal[3] = 3
    command_id: Identity
    action: Literal["submit_oco", "request_cancel_oco", "ack_cancel_oco"]
    reason_reference: Identity
    account_id: Identity
    strategy_id: Identity
    instrument_id: Identity
    position_id: Identity
    group_id: Digest | None = None
    quantity: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    target_price: PositiveDecimal | None = None

    @model_validator(mode="after")
    def parameters(self) -> Self:
        if self.action == "submit_oco":
            if self.group_id is not None or any(x is None for x in (self.quantity, self.stop_price, self.target_price)):
                raise ValueError("creation requires quantity and both prices; group ID is derived")
        elif self.group_id is None or any(x is not None for x in (self.quantity, self.stop_price, self.target_price)):
            raise ValueError("group cancellation forbids order amendments")
        return self


class OCOProgress(PaperContract):
    schema_version: Literal[3] = 3
    policy: Literal["oco-stop-first-quote-v3"] = "oco-stop-first-quote-v3"
    group_id: Digest
    account_id: Identity
    strategy_id: Identity
    instrument_id: Identity
    position_id: Identity
    creation_id: Identity
    original_quantity: PositiveDecimal
    remaining_quantity: NonNegativeDecimal
    state: Literal["active", "cancel_pending", "closed", "cancelled"]
    revision: Annotated[int, Field(ge=1)]
    last_sequence: Annotated[int, Field(gt=0)]
    timestamp: UtcTimestamp
    pending_request_id: Identity | None = None
    stop: OCOKernelProgress
    target: OCOKernelProgress

    @property
    def active(self):
        return self.state in ("active", "cancel_pending")

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        a, b = self.stop, self.target
        with localcontext(exact_context()):
            if (self.remaining_quantity != self.original_quantity - a.filled_quantity - b.filled_quantity
                    or a.withdrawn_quantity != b.filled_quantity or b.withdrawn_quantity != a.filled_quantity):
                raise ValueError("OCO shared quantity does not reconcile")
        if a.order_id == b.order_id or a.config.session_id == b.config.session_id:
            raise ValueError("OCO children require distinct identities")
        for child, role in ((a, "stop_loss"), (b, "take_profit")):
            c, s = child.config, child.submission
            if (s is None or c.group_id != self.group_id or c.account_id != self.account_id
                    or c.strategy_id != self.strategy_id or c.position_id != self.position_id
                    or c.instrument.instrument_id != self.instrument_id or c.child_role != role
                    or s.quantity != self.original_quantity or s.side is not a.submission.side
                    or child.last_sequence != self.last_sequence or child.timestamp != self.timestamp
                    or child.remaining_quantity != self.remaining_quantity):
                raise ValueError("OCO child ownership/clock mismatch")
        if a.config.liquidity_per_observation != b.config.liquidity_per_observation:
            raise ValueError("OCO liquidity policy mismatch")
        if self.active and (self.remaining_quantity == 0 or a.terminated or b.terminated):
            raise ValueError("active OCO requires two active children and live quantity")
        if self.state == "closed" and (self.remaining_quantity != 0
                or {a.state, b.state} != {OrderState.FILLED, OrderState.CANCELLED}):
            raise ValueError("closed OCO requires winner and cancelled sibling")
        if self.state == "cancelled" and (not a.terminated or not b.terminated):
            raise ValueError("cancelled OCO retains an active child")
        if (self.state == "cancel_pending") != (self.pending_request_id is not None):
            raise ValueError("OCO cancellation acknowledgement mismatch")
        if self.state == "cancel_pending" and (a.pending_cancellation is None or b.pending_cancellation is None):
            raise ValueError("pending OCO requires both child requests")
        return self


class OCOEvent(ContentRecord):
    namespace = "paper-oco-event-v3"
    schema_version: Literal[3] = 3
    input_id: Identity
    input_digest: Digest
    previous_revision: Annotated[int, Field(ge=0)]
    group: OCOProgress
    stop_records: tuple[PaperEvent, ...]
    target_records: tuple[PaperEvent, ...]
    financial_ids: tuple[Digest, ...] = Field(default=(), max_length=1)

    @property
    def orders(self):
        # Cross-child causation is explicit: the actual fill precedes its peer's
        # withdrawal/cancellation in the flat session audit projection.
        if any(isinstance(e, OCOFillRecord) for e in self.target_records):
            return (*self.target_records, *self.stop_records)
        return (*self.stop_records, *self.target_records)

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        if self.group.revision != self.previous_revision + 1:
            raise ValueError("OCO revision mismatch")
        for head, records in ((self.group.stop, self.stop_records), (self.group.target, self.target_records)):
            first = head.event_count - len(records) + 1
            if any(e.order_id != head.order_id or e.sequence != first + i
                    or e.input_sequence != self.group.last_sequence or e.timestamp != self.group.timestamp
                    for i, e in enumerate(records)):
                raise ValueError("OCO event does not bind child deltas")
        fills = [e for e in (*self.stop_records, *self.target_records) if isinstance(e, OCOFillRecord)]
        if len(fills) != len(self.financial_ids) or len(fills) > 1:
            raise ValueError("OCO event settlement count mismatch")
        return self


class OCOSnapshot(PaperContract):
    group: OCOProgress
    stop: KernelSnapshot
    target: KernelSnapshot

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        fills = {e.event_id: e for child in (self.stop, self.target)
            for e in child.events if isinstance(e, OCOFillRecord)}
        for child, head in ((self.stop, self.group.stop), (self.target, self.group.target)):
            if (child.config != head.config or child.submission != head.submission
                    or child.state is not head.state or child.order_id != head.order_id
                    or len(child.events) != head.event_count or len(child.inputs) != head.input_count
                    or child.last_sequence != head.last_sequence or child.timestamp != head.timestamp
                    or child.filled_quantity != head.filled_quantity
                    or child.withdrawn_quantity != head.withdrawn_quantity
                    or child.cumulative_costs != head.cumulative_costs or child.market != head.market
                    or child.pending_cancellation != head.pending_cancellation):
                raise ValueError("OCO snapshot/head mismatch")
            batches = {}
            for e in child.events:
                batches.setdefault(e.input_sequence, []).append(e)
            digest = "0"*64
            for item in child.inputs:
                events = batches.pop(item.sequence, ())
                if any(e.timestamp != item.timestamp for e in events):
                    raise ValueError("OCO snapshot event/input clock mismatch")
                digest = stable_id("paper-order-history-v2", (digest, item.canonical_json(),
                    tuple(e.canonical_json() for e in events)))
            if batches or digest != head.history_digest:
                raise ValueError("OCO snapshot prefix digest mismatch")
            for cls, cached in ((OrderActivation, head.activation), (StopTrigger, head.trigger)):
                actual = next((e for e in child.events if isinstance(e, cls)), None)
                if cached != actual:
                    raise ValueError("OCO snapshot cached causal record mismatch")
            adjustments = [e for e in child.events if isinstance(e, OCOQuantityAdjustment)]
            ids = [e.peer_fill.event_id for e in adjustments]
            peer = self.target if child is self.stop else self.stop
            peer_ids = {e.event_id for e in peer.events if isinstance(e, OCOFillRecord)}
            if len(ids) != len(set(ids)) or set(ids) != peer_ids or any(fills.get(e.peer_fill.event_id) != e.peer_fill for e in adjustments):
                raise ValueError("OCO sibling fill reconciliation mismatch")
        with localcontext(exact_context()):
            budgets = {}
            for f in fills.values():
                key = f.source.quote.model_dump_json()
                budgets[key] = budgets.get(key, 0) + f.execution.quantity
            budget = self.group.stop.config.liquidity_per_observation
            if budget is not None and any(q > budget for q in budgets.values()):
                raise ValueError("OCO shared liquidity exceeded")
        return self
