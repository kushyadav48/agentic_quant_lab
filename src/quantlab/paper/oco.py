"""Bounded two-child coordinator. One account publication; no history traversal."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from quantlab.backtesting import ExecutionCostConfig
from quantlab.risk import RiskConfig
from .account_models import exact_context
from .errors import PaperIdentityConflict, PaperInputError
from .history import RetainedMap
from .models import (MarketDelivery, OCOKernelConfig, OCOOrderSubmission, OCOFillRecord,
    OCOQuantityAdjustment, OrderSide, OrderState, OrderTransition, CancellationOutcome,
    CancellationRequest, CancellationAcknowledgement, stable_id)
from .oco_models import OCOCommand, OCOProgress, OCOEvent
from .orders import PaperOrderKernel, _KernelState, _KernelPreparation
from .strategy_models import record

ACTIVE = (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)


@dataclass(frozen=True)
class _OCOState:
    head: OCOProgress
    seen: RetainedMap


def validate_publication(snapshot, orders, group):
    h = group.head
    for head in (h.stop, h.target):
        state = orders.get(head.config.session_id)
        if state is None or state.snapshot.progress() != head:
            raise PaperInputError("OCO publication does not bind child state")
    p = snapshot.position
    if h.active and (p is None or p.entry_transaction_id != h.position_id
            or p.account_id != h.account_id or p.strategy_id != h.strategy_id
            or p.instrument.instrument_id != h.instrument_id or p.quantity != h.remaining_quantity):
        raise PaperInputError("OCO publication does not bind live position")
    if h.state == "closed" and p is not None and p.entry_transaction_id == h.position_id and p.quantity != 0:
        raise PaperInputError("closed OCO retains live position")


def _input(item, seen):
    identity = getattr(item, "event_id", None) if type(item) is MarketDelivery else getattr(item, "command_id", None)
    try:
        if type(item) not in (MarketDelivery, OCOCommand):
            raise ValueError("canonical OCO input required")
        item = type(item).model_validate(item)
        wire = item.canonical_json()
    except (ValueError, TypeError) as exc:
        if type(identity) is str and identity in seen:
            raise PaperIdentityConflict("OCO identity reused with invalid content") from exc
        raise PaperInputError("invalid OCO input") from exc
    prior = seen.get(identity)
    if prior is not None and wire != prior[0]:
        raise PaperIdentityConflict("OCO identity reused with different content")
    return item, identity, wire, None if prior is None else prior[1]


def _ownership(command, h):
    if (command.account_id, command.strategy_id, command.instrument_id, command.position_id) != (
            h.account_id, h.strategy_id, h.instrument_id, h.position_id):
        raise PaperInputError("OCO command ownership mismatch")


def _event(identity, item, h, previous, a, b, financial=()):
    return record(OCOEvent, input_id=identity, input_digest=stable_id("paper-oco-input-v3", item),
        previous_revision=previous, group=h, stop_records=a, target_records=b,
        financial_ids=tuple(p.event.event_id for p in financial))


def create_group(account, command, *, risk, costs, liquidity_per_observation, maximum_age, maximum_inputs):
    before = account._publication
    seen = RetainedMap() if before.oco is None else before.oco.seen
    command, identity, wire, prior = _input(command, seen)
    if type(command) is not OCOCommand or command.action != "submit_oco":
        raise PaperInputError("expected OCO creation command")
    if prior is not None:
        return prior
    p, s = before.snapshot.position, before.snapshot
    age = timedelta(seconds=60) if maximum_age is None else maximum_age
    from .accounts import MAX_KERNELS
    if (p is None or p.quantity == 0 or s.reservations or command.sequence < 2
            or command.timestamp < s.timestamp or command.timestamp - p.valuation.quote.timestamp > age
            or command.quantity != p.quantity or command.sequence <= p.valuation.sequence
            or (command.account_id, command.strategy_id, command.instrument_id, command.position_id) !=
                (p.account_id, p.strategy_id, p.instrument.instrument_id, p.entry_transaction_id)
            or before.oco is not None and (before.oco.head.active or before.oco.head.position_id == command.position_id)
            or any(k._view.state in ACTIVE for k, _ in account._kernels.values())
            or len(account._kernels) + 2 > MAX_KERNELS):
        raise PaperInputError("OCO requires exclusive fresh settled position ownership")
    long = p.direction.value == "long"
    reference = p.valuation.quote.bid if long else p.valuation.quote.ask
    if not (command.stop_price < reference < command.target_price if long
            else command.target_price < reference < command.stop_price):
        raise PaperInputError("OCO prices must strictly bracket the current executable side")
    group_id = stable_id("paper-oco-group-v3", command)
    seed = MarketDelivery(event_id=stable_id("paper-oco-seed-v3", group_id),
        sequence=command.sequence - 1, timestamp=command.timestamp,
        delivered_at=p.valuation.delivered_at, quote=p.valuation.quote)
    kernels, states, batches = [], [], []
    for role, order_type in (("stop_loss", "stop_market"), ("take_profit", "limit")):
        config = OCOKernelConfig(session_id=stable_id("paper-oco-child-session-v3", (group_id, role)),
            group_id=group_id, account_id=p.account_id, strategy_id=p.strategy_id, position_id=command.position_id,
            child_role=role, instrument=s.config.instrument, flat_equity=s.equity,
            running_peak_equity=s.running_peak_equity, risk=RiskConfig() if risk is None else risk,
            costs=ExecutionCostConfig() if costs is None else costs, liquidity_per_observation=liquidity_per_observation,
            maximum_age=age, maximum_inputs=maximum_inputs)
        k = PaperOrderKernel(config)
        initial = k._current_state()
        k._bind_account(account)
        seeded = k._prepare(seed, _before=initial)
        child = OCOOrderSubmission(sequence=command.sequence, timestamp=command.timestamp,
            command_id=stable_id("paper-oco-child-command-v3", (group_id, role)), causation_id=seed.event_id,
            instrument_id=command.instrument_id, side=OrderSide.SELL if long else OrderSide.BUY,
            quantity=command.quantity, order_type=order_type,
            stop_price=command.stop_price if role == "stop_loss" else None,
            limit_price=command.target_price if role == "take_profit" else None,
            position_id=command.position_id, protective_role=role, group_id=group_id)
        prepared = k._prepare(child, _before=seeded.after)
        kernels.append(k)
        states.append(prepared.after)
        batches.append(prepared.records)
    h = OCOProgress(group_id=group_id, account_id=p.account_id, strategy_id=p.strategy_id,
        instrument_id=command.instrument_id, position_id=command.position_id, creation_id=identity,
        original_quantity=command.quantity, remaining_quantity=command.quantity, state="active",
        revision=1, last_sequence=command.sequence, timestamp=command.timestamp,
        stop=states[0].snapshot.progress(), target=states[1].snapshot.progress())
    event = _event(identity, command, h, 0, *batches)
    group = _OCOState(h, seen.set(identity, (wire, event)))
    orders = dict(before.orders)
    # Registry admission is reversible and not visible through the old root.
    admitted = []
    try:
        for k, state in zip(kernels, states):
            session = k._config.session_id
            if session in account._kernels:
                raise PaperIdentityConflict("OCO child session collision")
            admitted.append(session)
            account._kernels[session] = (k, p.strategy_id)
            orders[session] = state
        account._publish(before, s, orders, oco=group)
    except BaseException:
        # An interrupted publication handoff must revoke the root before removing
        # its admitted bindings. Creation has no financial/cache side effects.
        object.__setattr__(account, "_publication", before)
        for session in admitted:
            account._kernels.pop(session, None)
        raise
    return event


def _extend(prepared, records, item, state):
    """Replace only the newly staged chunk, never concatenate a retained prefix."""
    before, proposed = prepared.before, prepared.after.snapshot
    records = (*prepared.records, *records)
    wire = item.canonical_json()
    view = before.snapshot.append(item, wire, records, state=state, command=proposed.submission,
        order_id=proposed.order_id, market=proposed.market)
    identity = item.event_id if type(item) is MarketDelivery else item.command_id
    return _KernelPreparation(before, _KernelState(view, before.seen.set(identity, (wire, records))), records)


def _record(cls, view, item, offset=0, **values):
    body = dict(kind=cls.model_fields["kind"].default, session_id=view.config.session_id,
        config_digest=stable_id("paper-config-v1", view.config), order_id=view.order_id,
        sequence=len(view.events) + offset + 1, input_sequence=item.sequence, timestamp=item.timestamp, **values)
    return cls(event_id=stable_id("paper-event-v1", body), **body)


def _cancel(prepared, item, cause, reason):
    view = prepared.after.snapshot
    if view.state not in ACTIVE:
        return prepared
    transition = _record(OrderTransition, view, item, causation_id=cause,
        previous_state=view.state, state=OrderState.CANCELLED, reason=reason)
    outcome = _record(CancellationOutcome, view, item, 1, causation_id=transition.event_id,
        state=OrderState.CANCELLED, cancelled=True, reason="cancelled")
    return _extend(prepared, (transition, outcome), item, OrderState.CANCELLED)


def _reconcile(prepared, item, fill):
    view = prepared.after.snapshot
    with localcontext(exact_context()):
        adjustment = _record(OCOQuantityAdjustment, view, item, causation_id=fill.event_id,
            group_id=fill.group_id, peer_fill=fill, previous_remaining=view.remaining_quantity,
            remaining_quantity=view.remaining_quantity - fill.execution.quantity,
            withdrawn_quantity=view.withdrawn_quantity + fill.execution.quantity)
    updated = _extend(prepared, (adjustment,), item, view.state)
    return _cancel(updated, item, adjustment.event_id, "oco_closed") if adjustment.remaining_quantity == 0 else updated


def process_group(account, group_id, item):
    before = account._publication
    group = before.oco
    if group is None:
        raise PaperInputError("unknown OCO group")
    item, identity, wire, prior = _input(item, group.seen)
    if prior is not None and prior.group.group_id == group_id:
        return prior
    if group.head.group_id != group_id or prior is not None:
        raise PaperInputError("unknown OCO group")
    h = group.head
    if item.sequence <= h.last_sequence or item.timestamp < max(h.timestamp, before.snapshot.timestamp):
        raise PaperInputError("OCO chronology must advance recorded sequence")
    validate_publication(before.snapshot, before.orders, group)
    if type(item) is OCOCommand:
        _ownership(item, h)
        if item.group_id != group_id or item.action == "submit_oco":
            raise PaperInputError("OCO command targets wrong group")
        if item.action == "ack_cancel_oco" and h.active and h.pending_request_id is None:
            raise PaperInputError("group acknowledgement requires a recorded request")
    kernels = [account._kernels[head.config.session_id][0] for head in (h.stop, h.target)]
    prepared, child_items = [], []
    pending = h.pending_request_id
    financial = ()
    fill = None
    for k in kernels:
        if type(item) is MarketDelivery:
            child_item = item
        else:
            requesting = item.action == "request_cancel_oco"
            cls = CancellationRequest if requesting else CancellationAcknowledgement
            old = k._view
            cause = old.activation.event_id if requesting or old.pending_cancellation is None else old.pending_cancellation.request_id
            child_item = cls(command_id=stable_id("paper-oco-cancellation-v3", (identity, k._config.child_role)),
                causation_id=cause, order_id=old.order_id, sequence=item.sequence, timestamp=item.timestamp)
        # The stop child has priority only on this recorded quote. A committed fill
        # exhausts its budget or the position, so the second child receives cap 0.
        cap = h.remaining_quantity if h.active and fill is None else Decimal("0")
        proposal = k._prepare(child_item, _quantity_cap=cap)
        own_fill = next((e for e in proposal.records if isinstance(e, OCOFillRecord)), None)
        if own_fill is not None:
            # The financial engine rechecks direction, quantity and provenance.
            # Any preparation failure aborts the entire group, without publication.
            settlement = account._prepare_apply(account._execution_input(proposal.after.snapshot, own_fill))
            if settlement.replayed:
                raise PaperIdentityConflict("OCO fill was already settled")
            financial = (settlement,)
            fill = own_fill
        prepared.append(proposal)
        child_items.append(child_item)
    if fill is not None:
        index = 1 if fill.order_id == h.stop.order_id else 0
        prepared[index] = _reconcile(prepared[index], child_items[index], fill)
    with localcontext(exact_context()):
        remaining = h.remaining_quantity - (Decimal("0") if fill is None else fill.execution.quantity)
    status = h.state
    if fill is not None and remaining == 0:
        status, pending = "closed", None
    elif type(item) is OCOCommand and h.active:
        if item.action == "request_cancel_oco":
            status, pending = "cancel_pending", identity if pending is None else pending
        else:
            status, pending = "cancelled", None
    values = h.model_dump(mode="python")
    values.update(remaining_quantity=remaining, state=status, pending_request_id=pending,
        revision=h.revision + 1, last_sequence=item.sequence, timestamp=item.timestamp,
        stop=prepared[0].after.snapshot.progress(), target=prepared[1].after.snapshot.progress())
    head = OCOProgress(**values)
    event = _event(identity, item, head, h.revision, prepared[0].records, prepared[1].records, financial)
    updated = _OCOState(head, group.seen.set(identity, (wire, event)))
    orders = dict(before.orders)
    for k, proposal in zip(kernels, prepared):
        orders[k._config.session_id] = proposal.after
    liquidity_entry = None
    budget = head.stop.config.liquidity_per_observation
    if fill is not None and budget is not None:
        liquidity_entry = (stable_id("paper-simulated-observation-v2", fill.source.quote), budget, fill.execution.quantity)
    account._publish(before, financial[0].after if financial else before.snapshot, orders, financial,
        liquidity_entry=liquidity_entry, oco=updated)
    return event
