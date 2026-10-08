"""Serialized, synchronous, one-order offline kernel.

SUBMITTED -> ACCEPTED -> FILLED; SUBMITTED -> REJECTED; ACCEPTED -> CANCELLED.
Risk rejection/error before execution cancels the accepted order. No callbacks,
wall clock, account updates, strategy interpretation, threads or transport exist.
Use one instance from one serialized caller; this is not a concurrent service.
"""
from pydantic import ValidationError

from quantlab.risk import RiskAction, RiskContext, RiskDecision, RiskSide, evaluate_entry_risk
from .errors import PaperIdentityConflict, PaperInputError
from .models import (
    CancellationOutcome, CancellationRequest, FillRecord, KernelConfig, KernelSnapshot,
    MarketDelivery, OrderSide, OrderState, OrderSubmission, OrderTransition, PaperEvent, RiskOutcome,
    stable_id,
)
from .pricing import price_quote


class PaperOrderKernel:
    """Own immutable configuration, retained inputs and exactly one entry order."""

    def __init__(self, config: KernelConfig) -> None:
        try:
            self._config = KernelConfig.model_validate(config)
            self._config_digest = stable_id("paper-config-v1", self._config.model_dump(mode="python"))
        except (ValueError, TypeError) as exc:
            raise PaperInputError("invalid paper kernel configuration") from exc
        self._snapshot = KernelSnapshot(config=self._config)
        self._seen: dict[str, tuple[str, tuple[PaperEvent, ...]]] = {}

    @property
    def snapshot(self) -> KernelSnapshot:
        return self._snapshot

    def _risk(self, command, source, timestamp):
        """Build context internally and validate the service output; errors deny."""
        try:
            context = RiskContext(signal_time=command.timestamp, execution_time=timestamp,
                side=RiskSide.LONG if command.side is OrderSide.BUY else RiskSide.SHORT,
                requested_quantity=command.quantity,
                reference_price=source.quote.ask if command.side is OrderSide.BUY else source.quote.bid,
                current_equity=self._config.flat_equity,
                running_peak_equity=self._config.running_peak_equity)
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
        if type(item) not in (MarketDelivery, OrderSubmission, CancellationRequest):
            raise PaperInputError("expected a canonical paper input")
        supplied_identity = item.event_id if isinstance(item, MarketDelivery) else item.command_id
        try:
            item = type(item).model_validate(item)
            wire = item.canonical_json()
        except (ValueError, TypeError, ValidationError) as exc:
            if type(supplied_identity) is str and supplied_identity in self._seen:
                raise PaperIdentityConflict("retained identity reused with invalid content") from exc
            raise PaperInputError("invalid paper input contract") from exc
        identity = item.event_id if isinstance(item, MarketDelivery) else item.command_id
        existing = self._seen.get(identity)
        if existing is not None:
            if wire != existing[0]:
                raise PaperIdentityConflict("identity reused with different input content")
            return existing[1]
        old = self._snapshot
        if identity in {e.event_id for e in old.events}:
            raise PaperIdentityConflict("input identity collides with an execution event")
        if item.sequence <= old.last_sequence or old.timestamp is not None and item.timestamp < old.timestamp:
            raise PaperInputError("input sequence/time must preserve logical chronology")
        known = set(self._seen) | {e.event_id for e in old.events}
        if not isinstance(item, MarketDelivery) and item.causation_id not in known:
            raise PaperInputError("command requires a retained causation reference")

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
            if state is OrderState.ACCEPTED and item.quote.timestamp >= command.timestamp:
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
            if market is None or item.causation_id not in {
                    event.event_id for event in old.inputs if isinstance(event, MarketDelivery)}:
                raise PaperInputError("submission requires a retained market cause")
            if item.instrument_id != self._config.instrument.instrument_id:
                raise PaperInputError("submission instrument does not match the kernel")
            numerator, denominator = item.quantity.as_integer_ratio()
            step_numerator, step_denominator = self._config.instrument.quantity_increment.as_integer_ratio()
            if (numerator * step_denominator) % (denominator * step_numerator):
                raise PaperInputError("quantity must be a multiple of instrument.quantity_increment")
            command = item
            order_id = stable_id("paper-order-v1", (self._config_digest, item.model_dump(mode="python")))
            submitted = transition(OrderState.SUBMITTED)
            risk, allowed = gate("acceptance", submitted.event_id)
            transition(OrderState.ACCEPTED if allowed else OrderState.REJECTED,
                None if allowed else ("risk_error" if risk.error else "risk_rejected"), risk.event_id)
        else:
            if command is None or item.order_id != order_id:
                raise PaperInputError("cancellation requires this kernel's order")
            cancelled = state is OrderState.ACCEPTED
            cause = transition(OrderState.CANCELLED, "cancel_requested").event_id if cancelled else identity
            record(CancellationOutcome, cause=cause, state=state, cancelled=cancelled,
                reason="cancelled" if cancelled else "terminal_order")

        updated = KernelSnapshot(config=self._config, last_sequence=item.sequence,
            timestamp=item.timestamp, market=market, submission=command, order_id=order_id,
            state=state, inputs=(*old.inputs, item), events=(*old.events, *records))
        # Check bounded serialization before publishing any state or retry identity.
        updated.canonical_json()
        result = tuple(records)
        seen = {**self._seen, identity: (wire, result)}
        self._snapshot, self._seen = updated, seen
        return result
