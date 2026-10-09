"""Serialized, synchronous, one-order offline kernel with explicit v1/v2 policies.

SUBMITTED -> ACCEPTED -> FILLED; SUBMITTED -> REJECTED; ACCEPTED -> CANCELLED.
Risk rejection/error before execution cancels the accepted order. Standalone
instances retain order-only Phase 18A behavior. Account-owned instances privately
delegate publication to their deterministic account owner; no arbitrary execution
callback, wall clock, strategy interpretation, threads or transport exist.
Use one instance from one serialized caller; this is not a concurrent service.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from quantlab._decimal import deterministic_context

from pydantic import ValidationError

from quantlab.risk import RiskAction, RiskContext, RiskDecision, RiskSide, evaluate_entry_risk
from .errors import PaperIdentityConflict, PaperInputError
from .models import (
    CancellationOutcome, CancellationRequest, FillRecord, KernelConfig, KernelSnapshot,
    MarketDelivery, OrderSide, OrderState, OrderSubmission, OrderTransition, PaperEvent, RiskOutcome,
    stable_id, AdvancedKernelConfig, AdvancedOrderSubmission, AdvancedFillRecord,
    OrderActivation, StopTrigger, PendingCancellation, CancellationAcknowledgement,
    OCOKernelConfig, OCOOrderSubmission, OCOFillRecord,
)
from .pricing import price_quote
from .history import RetainedMap
from .order_state import OrderStateView


@dataclass(frozen=True)
class _KernelState:
    snapshot: OrderStateView
    seen: RetainedMap


@dataclass(frozen=True)
class _KernelPreparation:
    before: _KernelState
    after: _KernelState
    records: tuple[PaperEvent, ...]
    replayed: bool = False


class PaperOrderKernel:
    """Own immutable configuration and one v1 entry or bounded v2 order."""

    def __init__(self, config: KernelConfig) -> None:
        try:
            self._config = (OCOKernelConfig if type(config) is OCOKernelConfig else AdvancedKernelConfig if type(config) is AdvancedKernelConfig else KernelConfig).model_validate(config)
            self._config_digest = stable_id("paper-config-v1", self._config.model_dump(mode="python"))
        except (ValueError, TypeError) as exc:
            raise PaperInputError("invalid paper kernel configuration") from exc
        self._snapshot = OrderStateView(config=self._config)
        self._seen = RetainedMap()
        self._account_owner = None

    @property
    def snapshot(self) -> KernelSnapshot:
        state = self._current_state().snapshot
        return state.export() if isinstance(state, OrderStateView) else state

    @property
    def _view(self):
        return self._current_state().snapshot

    def _progress(self):
        state = self._view
        return state.progress() if isinstance(state.config, AdvancedKernelConfig) else state.export()

    def _current_state(self) -> _KernelState:
        if self._account_owner is not None:
            return self._account_owner._owned_state(self)
        return _KernelState(self._snapshot, self._seen)

    def _bind_account(self, owner) -> None:
        """Private construction boundary; owned kernels cannot publish alone."""
        if self._account_owner is not None or self._snapshot.inputs:
            raise PaperInputError("only a fresh kernel may bind an account")
        self._account_owner = owner

    def _publish_standalone(self, prepared: _KernelPreparation) -> None:
        if self._account_owner is not None:
            raise PaperInputError("account-owned kernels require coordinated publication")
        if (self._snapshot is not prepared.before.snapshot
                or self._seen is not prepared.before.seen):
            raise PaperInputError("stale kernel preparation")
        self._snapshot, self._seen = prepared.after.snapshot, prepared.after.seen

    def _risk(self, command, source, timestamp):
        """Build context internally and validate the service output; errors deny."""
        try:
            equity, peak = self._config.flat_equity, self._config.running_peak_equity
            if isinstance(self._config, AdvancedKernelConfig) and self._account_owner is not None:
                account = self._account_owner.snapshot
                equity, peak = account.equity, account.running_peak_equity
                position = account.position
                if position is not None and position.quantity > 0:
                    from .account_models import exact_context
                    with localcontext(exact_context()):
                        long = position.direction.value == "long"
                        mark = source.quote.bid if long else source.quote.ask
                        unrealized = (mark - position.entry_basis if long else position.entry_basis - mark) * position.quantity
                        equity = account.balance + unrealized
                        peak = max(peak, equity)
            context = RiskContext(signal_time=command.timestamp, execution_time=timestamp,
                side=RiskSide.LONG if command.side is OrderSide.BUY else RiskSide.SHORT,
                requested_quantity=command.quantity,
                reference_price=source.quote.ask if command.side is OrderSide.BUY else source.quote.bid,
                current_equity=equity, running_peak_equity=peak)
            decision = RiskDecision.model_validate(evaluate_entry_risk(context, self._config.risk))
            if decision.model_dump(exclude={"action", "approved_quantity", "reasons"}) != context.model_dump():
                raise ValueError("risk output changed the trusted context")
            return decision, None
        except Exception:
            # A risk engine failure is a recorded denial, never an implicit ALLOW.
            # Do not retain arbitrary exception diagnostics in deterministic records.
            return None, "risk_error"

    def process(self, item: MarketDelivery | OrderSubmission | CancellationRequest) -> tuple[PaperEvent, ...]:
        """Validate and stage the entire input before atomically committing.

        Exact identity retries return the original outputs even after termination.
        New inputs require increasing sequence and nondecreasing logical time.
        Quote observation/delivery chronology must also be nondecreasing; equal
        times with distinct identities are permitted. Earlier observed quotes
        delivered after submission are retained but cannot fill that order.
        """
        if self._account_owner is not None:
            return self._account_owner.process_order(self, item)
        prepared = self._prepare(item)
        self._publish_standalone(prepared)
        return prepared.records

    def _prepare(self, item, *, _before=None, _quantity_cap=None) -> _KernelPreparation:
        """Validate execution without publishing inputs, records or retry state."""
        before = self._current_state() if _before is None else _before
        seen_before = before.seen
        advanced = isinstance(self._config, AdvancedKernelConfig)
        submission_type = OCOOrderSubmission if isinstance(self._config, OCOKernelConfig) else AdvancedOrderSubmission
        allowed_types = (MarketDelivery, submission_type, CancellationRequest, CancellationAcknowledgement) if advanced else (MarketDelivery, OrderSubmission, CancellationRequest)
        if type(item) not in allowed_types:
            raise PaperInputError("expected a canonical paper input")
        supplied_identity = item.event_id if isinstance(item, MarketDelivery) else item.command_id
        try:
            item = type(item).model_validate(item)
            wire = item.canonical_json()
        except (ValueError, TypeError, ValidationError) as exc:
            if type(supplied_identity) is str and supplied_identity in seen_before:
                raise PaperIdentityConflict("retained identity reused with invalid content") from exc
            raise PaperInputError("invalid paper input contract") from exc
        identity = item.event_id if isinstance(item, MarketDelivery) else item.command_id
        existing = seen_before.get(identity)
        if existing is not None:
            if wire != existing[0]:
                raise PaperIdentityConflict("identity reused with different input content")
            return _KernelPreparation(before, before, existing[1], replayed=True)
        old = before.snapshot
        if identity in old.identities:
            raise PaperIdentityConflict("input identity collides with an execution event")
        if item.sequence <= old.last_sequence or old.timestamp is not None and item.timestamp < old.timestamp:
            raise PaperInputError("input sequence/time must preserve logical chronology")
        known_cause = (isinstance(item, MarketDelivery) or item.causation_id in seen_before
                       or item.causation_id in old.identities)
        if not known_cause:
            raise PaperInputError("command requires a retained causation reference")

        if advanced and len(old.inputs) >= self._config.maximum_inputs and not isinstance(item, (CancellationRequest, CancellationAcknowledgement)):
            raise PaperInputError("advanced kernel input capacity reached; cancel remaining order")
        if advanced and len(old.inputs) >= self._config.maximum_inputs:
            if not ((isinstance(item, CancellationRequest) and old.pending_cancellation is None and not old.terminated)
                    or (isinstance(item, CancellationAcknowledgement) and old.pending_cancellation is not None)):
                raise PaperInputError("advanced capacity allows only cancellation completion")
        state, command, order_id, market = old.state, old.submission, old.order_id, old.market
        records: list[PaperEvent] = []

        def record(cls, *, cause=identity, **values):
            body = dict(session_id=self._config.session_id, config_digest=self._config_digest,
                order_id=order_id, sequence=len(old.events) + len(records) + 1,
                input_sequence=item.sequence, timestamp=item.timestamp, causation_id=cause,
                kind=cls.model_fields["kind"].default, **values)
            event = cls(event_id=stable_id("paper-event-v1", body), **body)
            records.append(event)
            return event

        def transition(target, reason=None, cause=identity):
            nonlocal state
            event = record(OrderTransition, cause=cause, previous_state=state, state=target, reason=reason)
            state = target
            return event

        def gate(stage, cause):
            decision, error = self._risk(command, market, item.timestamp)
            event = record(RiskOutcome, cause=cause, stage=stage, source=market, decision=decision, error=error)
            return event, error is None and decision.action is RiskAction.ALLOW

        if isinstance(item, MarketDelivery):
            if item.quote.instrument_id != self._config.instrument.instrument_id:
                raise PaperInputError("market instrument does not match the kernel")
            if market is not None and (item.quote.timestamp < market.quote.timestamp
                    or item.delivered_at < market.delivered_at):
                raise PaperInputError("market observation/delivery chronology cannot go backwards")
            market = item
            if advanced:
                from .advanced import ACTIVE, price_advanced
                if state in ACTIVE and item.quote.timestamp >= command.timestamp and (
                        item.timestamp - item.quote.timestamp <= self._config.maximum_age):
                    activation, trigger = old.activation, old.trigger
                    buy = command.side is OrderSide.BUY
                    reference = item.quote.ask if buy else item.quote.bid
                    if command.stop_price is not None and trigger is None:
                        if reference >= command.stop_price if buy else reference <= command.stop_price:
                            record(StopTrigger, source=item, activation_id=activation.event_id,
                                stop_price=command.stop_price, side=command.side)
                    else:
                        if self._account_owner is not None and self._config.liquidity_per_observation is None:
                            self._account_owner._remaining_liquidity(item, None)
                        executable = command.limit_price is None or (
                            reference <= command.limit_price if buy else reference >= command.limit_price)
                        if trigger is not None and item.quote.timestamp < trigger.source.quote.timestamp:
                            executable = False
                        with localcontext(deterministic_context(prec=4096)):
                            quantity = old.remaining_quantity
                            if _quantity_cap is not None:
                                quantity = min(quantity, _quantity_cap)
                            if self._config.liquidity_per_observation is not None:
                                consumed = old.liquidity.get(stable_id("paper-simulated-observation-v2", item.quote), Decimal("0"))
                                remaining_budget = self._config.liquidity_per_observation - consumed
                                if self._account_owner is not None:
                                    remaining_budget = min(remaining_budget, self._account_owner._remaining_liquidity(
                                        item, self._config.liquidity_per_observation))
                                quantity = min(quantity, remaining_budget)
                        if executable and quantity > 0:
                            if command.reduce_only:
                                cause, allowed = activation.event_id, True
                            else:
                                risk, allowed = gate("execution", identity)
                                cause = risk.event_id
                            if not allowed:
                                transition(OrderState.CANCELLED,
                                    "risk_error" if risk.error else "risk_rejected", cause)
                            else:
                                execution = price_advanced(command, market, self._config.costs, quantity)
                                with localcontext(deterministic_context(prec=4096)):
                                    cumulative = old.filled_quantity + quantity
                                    remaining = command.quantity - cumulative - old.withdrawn_quantity
                                oco = isinstance(command, OCOOrderSubmission)
                                extra = dict(group_id=command.group_id, withdrawn_quantity=old.withdrawn_quantity) if oco else {}
                                fill = record(OCOFillRecord if oco else AdvancedFillRecord, **extra, cause=cause, side=command.side,
                                    submission=command, source=market, assumptions=self._config.costs,
                                    execution=execution, activation_id=activation.event_id,
                                    trigger_id=None if trigger is None else trigger.event_id,
                                    cumulative_quantity=cumulative, remaining_quantity=remaining,
                                    liquidity_policy="full-fill-assumption-v1" if self._config.liquidity_per_observation is None
                                        else "simulated-per-observation-v2")
                                transition(OrderState.FILLED if remaining == 0 else OrderState.PARTIALLY_FILLED,
                                    cause=fill.event_id)
                        if command.time_in_force == "ioc" and state in ACTIVE:
                            transition(OrderState.EXPIRED, "ioc_remaining")
            elif state is OrderState.ACCEPTED and item.quote.timestamp >= command.timestamp:
                risk, allowed = gate("execution", identity)
                if not allowed:
                    transition(OrderState.CANCELLED,
                        "risk_error" if risk.error else "risk_rejected", risk.event_id)
                else:
                    execution = price_quote(command, market, self._config.costs)
                    fill = record(FillRecord, cause=risk.event_id, side=command.side,
                        submission=command, source=market, assumptions=self._config.costs,
                        execution=execution)
                    transition(OrderState.FILLED, cause=fill.event_id)
        elif isinstance(item, OrderSubmission):
            if command is not None:
                raise PaperInputError("one submission per kernel; terminal orders cannot reopen")
            if market is None or item.causation_id not in old.market_ids:
                raise PaperInputError("submission requires a retained market cause")
            if item.instrument_id != self._config.instrument.instrument_id:
                raise PaperInputError("submission instrument does not match the kernel")
            numerator, denominator = item.quantity.as_integer_ratio()
            step_numerator, step_denominator = self._config.instrument.quantity_increment.as_integer_ratio()
            if (numerator * step_denominator) % (denominator * step_numerator):
                raise PaperInputError("quantity must be a multiple of instrument.quantity_increment")
            if advanced and item.reduce_only and self._account_owner is None:
                raise PaperInputError("reduce-only execution requires a position-owning account")
            if isinstance(self._config, OCOKernelConfig) and (item.group_id != self._config.group_id
                    or item.protective_role != self._config.child_role):
                raise PaperInputError("OCO child identity mismatch")
            if advanced and item.timestamp - market.quote.timestamp > self._config.maximum_age:
                raise PaperInputError("advanced activation requires a fresh eligible quote")
            command = item
            order_id = stable_id("paper-order-v1", (self._config_digest, item.model_dump(mode="python")))
            submitted = transition(OrderState.SUBMITTED)
            if advanced and (item.reduce_only != (self._config.position_id is not None)
                    or item.position_id != self._config.position_id):
                raise PaperInputError("submission does not match kernel position binding")
            if advanced and item.reduce_only:
                cause, allowed = submitted.event_id, True
            else:
                risk, allowed = gate("acceptance", submitted.event_id)
                cause = risk.event_id
            transition(OrderState.ACCEPTED if allowed else OrderState.REJECTED,
                None if allowed else ("risk_error" if risk.error else "risk_rejected"), cause)
            if advanced and allowed:
                record(OrderActivation, submission_id=item.command_id)
        else:
            if command is None or item.order_id != order_id:
                raise PaperInputError("cancellation requires this kernel's order")
            if advanced:
                from .advanced import ACTIVE
                if isinstance(item, CancellationAcknowledgement):
                    pending = old.pending_cancellation
                    if pending is None:
                        if not old.terminated:
                            raise PaperInputError("acknowledgement requires a pending cancellation")
                    elif item.causation_id != pending.request_id:
                        raise PaperInputError("acknowledgement requires the pending request identity")
                elif state in ACTIVE:
                    record(PendingCancellation, request_id=item.command_id if old.pending_cancellation is None
                        else old.pending_cancellation.request_id)
                    result = tuple(records)
                    updated = old.append(item, wire, result, state=state, command=command,
                        order_id=order_id, market=market)
                    return _KernelPreparation(before, _KernelState(updated,
                        seen_before.set(identity, (wire, result))), result)
            cancelled = state in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)
            cause = transition(OrderState.CANCELLED, "cancel_requested").event_id if cancelled else identity
            record(CancellationOutcome, cause=cause, state=state, cancelled=cancelled,
                reason="cancelled" if cancelled else "terminal_order")

        result = tuple(records)
        updated = old.append(item, wire, result, state=state, command=command, order_id=order_id, market=market)
        return _KernelPreparation(before, _KernelState(updated, seen_before.set(identity, (wire, result))), result)

    def _without_fill(self, prepared: _KernelPreparation, reason: str) -> _KernelPreparation:
        """Replace only a staged fill with an account denial; preserve risk/pricing causality."""
        if self._account_owner is None or prepared.replayed:
            raise PaperInputError("account denial requires a new owned preparation")
        fills = [e for e in prepared.records if isinstance(e, FillRecord)]
        if len(fills) != 1 or reason not in ("account_unfunded", "account_rejected"):
            raise PaperInputError("invalid coordinated non-fill outcome")
        fill = fills[0]
        prefix = tuple(e for e in prepared.records if e.sequence < fill.sequence)
        body = dict(kind="transition", session_id=fill.session_id,
            config_digest=fill.config_digest, order_id=fill.order_id,
            sequence=fill.sequence, input_sequence=fill.input_sequence,
            timestamp=fill.timestamp, causation_id=fill.causation_id,
            previous_state=prepared.before.snapshot.state, state=OrderState.CANCELLED, reason=reason)
        denial = OrderTransition(event_id=stable_id("paper-event-v1", body), **body)
        records = (*prefix, denial)
        proposed = prepared.after.snapshot
        item = proposed.inputs[-1]
        wire = item.canonical_json()
        updated = prepared.before.snapshot.append(item, wire, records, state=OrderState.CANCELLED,
            command=proposed.submission, order_id=proposed.order_id, market=proposed.market)
        return _KernelPreparation(prepared.before, _KernelState(updated,
            prepared.before.seen.set(item.event_id, (wire, records))), records)
