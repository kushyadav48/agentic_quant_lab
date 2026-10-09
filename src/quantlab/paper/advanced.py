"""Small v2 policies shared by the existing kernel, pricing and accounting owner."""
from decimal import Decimal, localcontext

from quantlab._decimal import deterministic_context
from quantlab.backtesting import Fill, SignalAction
from quantlab.backtesting.execution import price_execution
from .models import (AdvancedFillRecord, AdvancedOrderSubmission, FillRecord,
    OrderActivation, OrderState, PendingCancellation, StopTrigger, OCOOrderSubmission,
    OCOFillRecord, OCOQuantityAdjustment, OCOKernelConfig)

ACTIVE = (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)
TERMINAL = (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED)


def effective_costs(command, source, costs):
    """Cap adverse slippage at the limit; retain observed-side reference price."""
    buy = command.side.value == "buy"
    reference = source.quote.ask if buy else source.quote.bid
    with localcontext(deterministic_context(prec=4096)):
        slip = costs.slippage
        if command.limit_price is not None:
            room = command.limit_price - reference if buy else reference - command.limit_price
            if room < 0:
                raise ValueError("limit is not executable")
            slip = min(slip, room)
        return type(costs).model_validate({**costs.model_dump(), "slippage": slip})


def price_advanced(command, source, costs, quantity):
    buy = command.side.value == "buy"
    reference = source.quote.ask if buy else source.quote.bid
    effective = effective_costs(command, source, costs)
    price, breakdown = price_execution(reference, quantity, effective,
        buy=buy, spread_adjustment=Decimal("0"))
    action = (SignalAction.EXIT_SHORT if buy else SignalAction.EXIT_LONG) if command.reduce_only else (
        SignalAction.ENTER_LONG if buy else SignalAction.ENTER_SHORT)
    return Fill(action=action, signal_time=command.timestamp, execution_time=source.timestamp,
        reference_price=reference, execution_price=price, quantity=quantity,
        slippage_adjustment=effective.slippage, costs=breakdown)


def validate_fill(record):
    c, s = record.submission, record.source
    if (record.side is not c.side or s.sequence <= c.sequence or s.quote.timestamp < c.timestamp
            or s.quote.instrument_id != c.instrument_id or record.input_sequence != s.sequence
            or record.timestamp != s.timestamp or record.assumptions.spread != 0):
        raise ValueError("advanced fill lacks causal quote provenance")
    with localcontext(deterministic_context(prec=4096)):
        if (record.cumulative_quantity > c.quantity or
                record.remaining_quantity != c.quantity - record.cumulative_quantity -
                    (record.withdrawn_quantity if isinstance(record, OCOFillRecord) else Decimal("0")) or
                record.execution.quantity > record.cumulative_quantity):
            raise ValueError("advanced fill quantity does not reconcile")
    if (c.stop_price is not None) != (record.trigger_id is not None):
        raise ValueError("stop fill requires a trigger reference")
    if record.execution != price_advanced(c, s, record.assumptions, record.execution.quantity):
        raise ValueError("advanced fill economics mismatch")


def validate_snapshot(snapshot, state):
    c = snapshot.submission
    expected = OCOOrderSubmission if isinstance(snapshot.config, OCOKernelConfig) else AdvancedOrderSubmission
    if c is not None and type(c) is not expected:
        raise ValueError("v2 kernel requires a v2 submission")
    if expected is OCOOrderSubmission and c is not None:
        policy = snapshot.config
        if (c.group_id != policy.group_id or c.position_id != policy.position_id
                or c.protective_role != policy.child_role or c.instrument_id != policy.instrument.instrument_id):
            raise ValueError("OCO snapshot ownership mismatch")
    if len(snapshot.inputs) > snapshot.config.maximum_inputs + 2:
        raise ValueError("advanced input capacity exceeded")
    if state is not snapshot.state or snapshot.filled_quantity < 0 or snapshot.remaining_quantity < 0:
        raise ValueError("advanced quantity/state mismatch")
    activations = [e for e in snapshot.events if isinstance(e, OrderActivation)]
    triggers = [e for e in snapshot.events if isinstance(e, StopTrigger)]
    fills = [e for e in snapshot.events if isinstance(e, FillRecord)]
    if len(activations) > 1 or len(triggers) > 1 or (fills and len(activations) != 1):
        raise ValueError("activation/trigger cardinality mismatch")
    if snapshot.state in (OrderState.ACCEPTED, OrderState.REJECTED) and fills:
        raise ValueError("unfilled state cannot contain committed fills")
    if activations and (activations[0].submission_id != c.command_id or
            activations[0].input_sequence != c.sequence or activations[0].timestamp != c.timestamp):
        raise ValueError("activation must bind the retained submission")
    from .models import OrderTransition
    for index, event in enumerate(snapshot.events):
        if isinstance(event, AdvancedFillRecord):
            if index + 1 >= len(snapshot.events):
                raise ValueError("fill must have its atomic state transition")
            following = snapshot.events[index + 1]
            target = OrderState.FILLED if event.remaining_quantity == 0 else OrderState.PARTIALLY_FILLED
            if (not isinstance(following, OrderTransition) or following.state is not target
                    or following.causation_id != event.event_id or following.previous_state not in ACTIVE):
                raise ValueError("fill/state transition mismatch")
    if snapshot.state is OrderState.FILLED and snapshot.remaining_quantity != 0:
        raise ValueError("filled order retains quantity")
    if snapshot.state is OrderState.PARTIALLY_FILLED and not 0 < snapshot.filled_quantity < c.quantity:
        raise ValueError("partial state requires partial quantity")
    withdrawn = Decimal("0")
    total = Decimal("0")
    with localcontext(deterministic_context(prec=4096)):
        for f in snapshot.events:
            if isinstance(f, OCOQuantityAdjustment):
                if (expected is not OCOOrderSubmission or f.group_id != c.group_id
                        or f.peer_fill.submission.position_id != c.position_id
                        or f.peer_fill.side is not c.side
                        or f.peer_fill.submission.protective_role == c.protective_role
                        or f.previous_remaining != c.quantity - total - withdrawn
                        or f.withdrawn_quantity != withdrawn + f.peer_fill.execution.quantity):
                    raise ValueError("sibling withdrawal chain mismatch")
                withdrawn = f.withdrawn_quantity
                continue
            if not isinstance(f, FillRecord):
                continue
            if (expected is OCOOrderSubmission) != isinstance(f, OCOFillRecord):
                raise ValueError("OCO fill version mismatch")
            if isinstance(f, OCOFillRecord) and f.withdrawn_quantity != withdrawn:
                raise ValueError("fill ignores sibling withdrawals")
            if not isinstance(f, AdvancedFillRecord):
                raise ValueError("v2 requires advanced fill attribution")
            total += f.execution.quantity
            if (f.submission != c or f.cumulative_quantity != total or
                    f.activation_id != activations[0].event_id or
                    f.input_sequence <= activations[0].input_sequence):
                raise ValueError("fill activation/quantity chain mismatch")
            if c.stop_price is not None and (not triggers or f.trigger_id != triggers[0].event_id
                    or f.source.sequence <= triggers[0].source.sequence
                    or f.source.quote.timestamp < triggers[0].source.quote.timestamp):
                raise ValueError("stop fill must follow its trigger on a later delivery")
            if snapshot.config.liquidity_per_observation is None:
                if f.liquidity_policy != "full-fill-assumption-v1":
                    raise ValueError("missing declared liquidity policy")
            elif f.liquidity_policy != "simulated-per-observation-v2":
                raise ValueError("liquidity policy mismatch")
        if snapshot.config.liquidity_per_observation is not None:
            budgets = {}
            for f in fills:
                key = f.source.quote.canonical_json() if hasattr(f.source.quote, "canonical_json") else (
                    f.source.quote.model_dump_json())
                budgets[key] = budgets.get(key, Decimal("0")) + f.execution.quantity
            if any(q > snapshot.config.liquidity_per_observation for q in budgets.values()):
                raise ValueError("observation liquidity was overconsumed")
    for t in triggers:
        if (not activations or t.activation_id != activations[0].event_id or
                t.input_sequence <= activations[0].input_sequence or c.stop_price != t.stop_price
                or t.source.quote.timestamp < c.timestamp or t.side is not c.side):
            raise ValueError("trigger must follow valid activation")
