"""Trusted incremental v2 matching state; public history remains fully validated."""
from dataclasses import dataclass, field, replace
from functools import cached_property
from decimal import Decimal, localcontext

from quantlab._decimal import deterministic_context
from quantlab.backtesting import CostBreakdown
from .history import History, RetainedMap
from .models import (KernelSnapshot, KernelProgress, FillRecord, OrderActivation,
    StopTrigger, PendingCancellation, OrderTransition, OrderState, MarketDelivery, AdvancedKernelConfig,
    canonical_json, stable_id, OCOKernelConfig, OCOKernelProgress, OCOQuantityAdjustment)


@dataclass(frozen=True)
class OrderStateView:
    config: object
    last_sequence: int = 0
    timestamp: object = None
    market: object = None
    submission: object = None
    order_id: object = None
    state: object = None
    inputs: History = field(default_factory=History)
    events: History = field(default_factory=History)
    identities: RetainedMap = field(default_factory=RetainedMap)
    market_ids: RetainedMap = field(default_factory=RetainedMap)
    liquidity: RetainedMap = field(default_factory=RetainedMap)
    withdrawn_quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    cumulative_costs: CostBreakdown = field(default_factory=CostBreakdown)
    accepted: object = None
    transition: object = None
    first_fill: object = None
    activation: object = None
    trigger: object = None
    pending_cancellation: object = None
    history_digest: str = "0" * 64

    @property
    def terminated(self):
        return self.state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED)

    @property
    def remaining_quantity(self):
        with localcontext(deterministic_context(prec=4096)):
            return Decimal("0") if self.submission is None else self.submission.quantity - self.filled_quantity - self.withdrawn_quantity

    def values(self):
        return {name: getattr(self, name) for name in (
            "config", "last_sequence", "timestamp", "market", "submission", "order_id", "state")}

    @cached_property
    def full_snapshot(self):
        return KernelSnapshot(**self.values(), inputs=tuple(self.inputs), events=tuple(self.events))

    def export(self):
        return self.full_snapshot

    def progress(self):
        cls = OCOKernelProgress if isinstance(self.config, OCOKernelConfig) else KernelProgress
        extra = dict(withdrawn_quantity=self.withdrawn_quantity) if cls is OCOKernelProgress else {}
        return cls(**self.values(), **extra, input_count=len(self.inputs), event_count=len(self.events),
            filled_quantity=self.filled_quantity, cumulative_costs=self.cumulative_costs,
            activation=self.activation, trigger=self.trigger, pending_cancellation=self.pending_cancellation,
            history_digest=self.history_digest)

    def append(self, item, wire, records, *, state, command, order_id, market):
        event_wires = tuple(e.canonical_json() for e in records)
        identities, markets, liquidity = self.identities, self.market_ids, self.liquidity
        activation, trigger, pending = self.activation, self.trigger, self.pending_cancellation
        quantity, costs, first_fill = self.filled_quantity, self.cumulative_costs, self.first_fill
        withdrawn = self.withdrawn_quantity
        accepted, transition = self.accepted, self.transition
        for event in records:
            identities = identities.set(event.event_id, True)
            if isinstance(event, OrderTransition):
                transition = event
                if event.state is OrderState.ACCEPTED:
                    accepted = event
            elif isinstance(event, OrderActivation):
                activation = event
            elif isinstance(event, StopTrigger):
                trigger = event
            elif isinstance(event, PendingCancellation):
                pending = event
            elif isinstance(event, OCOQuantityAdjustment):
                withdrawn = event.withdrawn_quantity
            elif isinstance(event, FillRecord):
                first_fill = event if first_fill is None else first_fill
                with localcontext(deterministic_context(prec=4096)):
                    quantity += event.execution.quantity
                    costs = CostBreakdown(**{name: getattr(costs, name) + getattr(event.execution.costs, name)
                        for name in CostBreakdown.model_fields})
                    key = stable_id("paper-simulated-observation-v2", event.source.quote)
                    liquidity = liquidity.set(key, liquidity.get(key, Decimal("0")) + event.execution.quantity)
        if isinstance(item, MarketDelivery):
            markets = markets.set(item.event_id, True)
        if state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED):
            pending = None
        result = replace(self, last_sequence=item.sequence, timestamp=item.timestamp,
            market=market, submission=command, order_id=order_id, state=state,
            inputs=self.inputs.append((item,), (wire,)), events=self.events.append(records, event_wires),
            identities=identities, market_ids=markets, liquidity=liquidity,
            activation=activation, trigger=trigger, pending_cancellation=pending,
            filled_quantity=quantity, withdrawn_quantity=withdrawn, cumulative_costs=costs, first_fill=first_fill,
            accepted=accepted, transition=transition,
            history_digest=stable_id("paper-order-history-v2", (self.history_digest, wire, event_wires)))
        # Preserve the existing full-snapshot wire bound without traversing history.
        size = len(canonical_json(dict(result.values(), inputs=(), events=())).encode("utf-8"))
        if size + result.inputs.encoded_size + result.events.encoded_size > 4_194_304:
            raise ValueError("Canonical record exceeds serialization bound")
        if isinstance(self.config, AdvancedKernelConfig):
            result.progress().canonical_json()
        return result
