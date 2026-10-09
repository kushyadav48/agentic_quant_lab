"""Frozen offline order contracts; no strategy admission or cash semantics."""
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from quantlab.backtesting import CostBreakdown, ExecutionCostConfig, Fill, SignalAction
from quantlab.data import Instrument, MarketQuote
from quantlab.data.models import NonNegativeDecimal, PositiveDecimal, UtcTimestamp, _DomainModel
from quantlab.risk import RiskConfig, RiskDecision
from quantlab.risk.models import FiniteDecimal
from quantlab.strategies.schema import Digest

Identity = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^\S+$")]
Sequence = Annotated[int, Field(gt=0)]


def canonical_json(value) -> str:
    """Finite sorted JSON with exact Decimal spelling, independent of context.

    Decimal expansion (4096 digits) and total encoding (4 MiB) are bounded.
    This core serializer has no MCP dependency; wire floats are not supported.
    """
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="python")
        if isinstance(item, Decimal):
            _, digits, exponent = item.as_tuple()
            if not item.is_finite() or max(len(digits) + exponent, -exponent) > 4096:
                raise ValueError("Decimal exceeds canonical serialization bounds")
            if item == 0:
                return "0"
            text = format(item, "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        if isinstance(item, timedelta):
            return {"days": item.days, "seconds": item.seconds, "microseconds": item.microseconds}
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, Enum):
            return item.value
        raise TypeError("Unsupported canonical value")

    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False, default=encode)
    chunks = []
    size = 0
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > 4_194_304:
            raise ValueError("Canonical record exceeds serialization bound")
        chunks.append(chunk)
    return "".join(chunks)


def stable_id(namespace: str, value) -> str:
    """Content identity; callers supply stable namespaces, never a clock/random ID."""
    return hashlib.sha256(canonical_json((namespace, value)).encode("utf-8")).hexdigest()


class PaperContract(_DomainModel):
    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="python"))


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderState(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    EXPIRED = "expired"


class KernelConfig(PaperContract):
    """Trusted construction-time flat risk state, not cash or an account ledger.

    No per-command account context or policy override is accepted. Equity and
    peak remain fixed until the one order terminates. Quote-currency research
    units and multiplier 1 are the only supported valuation assumptions.
    """
    session_id: Identity
    instrument: Instrument
    flat_equity: FiniteDecimal
    running_peak_equity: PositiveDecimal
    risk: RiskConfig = Field(default_factory=RiskConfig)
    costs: ExecutionCostConfig = Field(default_factory=ExecutionCostConfig)

    @model_validator(mode="after")
    def supported(self) -> Self:
        if self.instrument.contract_multiplier != 1:
            raise ValueError("paper kernel requires contract_multiplier 1")
        if self.costs.spread != 0:
            raise ValueError("observed quotes do not support synthetic spread")
        if self.running_peak_equity < self.flat_equity:
            raise ValueError("running peak must include flat equity")
        return self


class AdvancedKernelConfig(KernelConfig):
    """Explicit v2 policy; legacy configuration and digests remain unchanged."""
    schema_version: Literal[2]
    policy: Literal["advanced-quote-v2"] = "advanced-quote-v2"
    liquidity_per_observation: PositiveDecimal | None = None
    maximum_age: timedelta = timedelta(seconds=60)
    maximum_inputs: Annotated[int, Field(ge=3, le=256)] = 128
    position_id: Identity | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.maximum_age < timedelta(0):
            raise ValueError("negative data age")
        if self.liquidity_per_observation is not None:
            n, d = self.liquidity_per_observation.as_integer_ratio()
            sn, sd = self.instrument.quantity_increment.as_integer_ratio()
            if (n * sd) % (d * sn):
                raise ValueError("liquidity budget must respect quantity increment")
        return self


class OCOKernelConfig(AdvancedKernelConfig):
    """Explicit v3 linked-child policy; v2 journals retain their semantics."""
    schema_version: Literal[3] = 3
    policy: Literal["oco-stop-first-quote-v3"] = "oco-stop-first-quote-v3"
    group_id: Digest
    account_id: Identity
    strategy_id: Identity
    position_id: Identity
    child_role: Literal["stop_loss", "take_profit"]


class LogicalInput(PaperContract):
    sequence: Sequence
    timestamp: UtcTimestamp  # Effective processing time supplied by the trusted producer.


class MarketDelivery(LogicalInput):
    kind: Literal["market"] = "market"
    event_id: Identity
    delivered_at: UtcTimestamp
    quote: MarketQuote

    @model_validator(mode="after")
    def available(self) -> Self:
        if self.delivered_at < self.quote.timestamp:
            raise ValueError("delivery cannot precede the market observation")
        if self.timestamp < max(self.delivered_at, self.quote.available_at):
            raise ValueError("processing requires both delivery and quote availability")
        return self


class OrderSubmission(LogicalInput):
    kind: Literal["submit"] = "submit"
    command_id: Identity
    causation_id: Identity
    instrument_id: Identity
    side: OrderSide
    quantity: PositiveDecimal
    order_type: Literal["market"] = "market"
    time_in_force: Literal["gtc"] = "gtc"


class AdvancedOrderSubmission(OrderSubmission):
    schema_version: Literal[2] = 2
    kind: Literal["submit_advanced"] = "submit_advanced"
    order_type: Literal["market", "limit", "stop_market", "stop_limit"] = "market"
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    reduce_only: bool = False
    position_id: Identity | None = None
    protective_role: Literal["stop_loss", "take_profit"] | None = None

    @model_validator(mode="after")
    def parameters(self) -> Self:
        if (self.limit_price is not None) != (self.order_type in ("limit", "stop_limit")):
            raise ValueError("limit price required only for limit orders")
        if (self.stop_price is not None) != (self.order_type in ("stop_market", "stop_limit")):
            raise ValueError("stop price required only for stop orders")
        if self.reduce_only != (self.position_id is not None):
            raise ValueError("reduce-only requires explicit position ownership")
        if self.protective_role is not None and (not self.reduce_only or
                self.order_type != ("stop_market" if self.protective_role == "stop_loss" else "limit")):
            raise ValueError("protective child requires its supported reduce-only type")
        if self.time_in_force == "ioc" and self.stop_price is not None:
            raise ValueError("IOC stop activation semantics are unsupported")
        return self


class OCOOrderSubmission(AdvancedOrderSubmission):
    schema_version: Literal[3] = 3
    kind: Literal["submit_oco_child"] = "submit_oco_child"
    group_id: Digest
    reduce_only: Literal[True] = True
    position_id: Identity
    protective_role: Literal["stop_loss", "take_profit"]
    time_in_force: Literal["gtc"] = "gtc"
    order_type: Literal["stop_market", "limit"]


class CancellationRequest(LogicalInput):
    kind: Literal["cancel"] = "cancel"
    command_id: Identity
    causation_id: Identity
    order_id: Digest


class CancellationAcknowledgement(LogicalInput):
    kind: Literal["cancel_ack"] = "cancel_ack"
    command_id: Identity
    causation_id: Identity
    order_id: Digest


KernelInput = Annotated[MarketDelivery | OrderSubmission | AdvancedOrderSubmission | OCOOrderSubmission |
    CancellationRequest | CancellationAcknowledgement,
    Field(discriminator="kind")]


class ExecutionRecord(PaperContract):
    """Output sequence is journal-local; input_sequence identifies its trigger."""
    event_id: Digest
    session_id: Identity
    config_digest: Digest
    order_id: Digest
    sequence: Sequence
    input_sequence: Sequence
    timestamp: UtcTimestamp
    causation_id: Identity

    @model_validator(mode="after")
    def content_identity(self) -> Self:
        body = self.model_dump(mode="python", exclude={"event_id"})
        if self.event_id != stable_id("paper-event-v1", body):
            raise ValueError("event ID must bind canonical record content")
        return self


TransitionReason = Literal["risk_rejected", "risk_error", "cancel_requested",
    "account_unfunded", "account_rejected", "ioc_remaining", "oco_closed", "oco_denied"]


class OrderTransition(ExecutionRecord):
    kind: Literal["transition"] = "transition"
    previous_state: OrderState | None
    state: OrderState
    reason: TransitionReason | None = None

    @model_validator(mode="after")
    def legal(self) -> Self:
        legal = {
            None: (OrderState.SUBMITTED,),
            OrderState.SUBMITTED: (OrderState.ACCEPTED, OrderState.REJECTED),
            OrderState.ACCEPTED: (OrderState.CANCELLED, OrderState.FILLED, OrderState.PARTIALLY_FILLED, OrderState.EXPIRED),
            OrderState.PARTIALLY_FILLED: (OrderState.CANCELLED, OrderState.FILLED, OrderState.PARTIALLY_FILLED, OrderState.EXPIRED),
        }
        if self.state not in legal.get(self.previous_state, ()):
            raise ValueError("illegal order transition")
        if self.state is OrderState.REJECTED:
            if self.reason not in ("risk_rejected", "risk_error"):
                raise ValueError("rejection requires a risk reason")
        elif self.state in (OrderState.CANCELLED, OrderState.EXPIRED):
            if self.reason is None:
                raise ValueError("cancellation requires a reason")
        elif self.reason is not None:
            raise ValueError("successful transition forbids a failure reason")
        return self


class RiskOutcome(ExecutionRecord):
    kind: Literal["risk"] = "risk"
    stage: Literal["acceptance", "execution"]
    source: MarketDelivery
    decision: RiskDecision | None = None
    error: Literal["risk_error"] | None = None

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if (self.decision is None) == (self.error is None):
            raise ValueError("risk requires exactly one decision or error")
        if self.source.sequence > self.input_sequence or self.source.timestamp > self.timestamp:
            raise ValueError("risk cannot reference a future market delivery")
        if self.decision is not None and self.decision.execution_time != self.timestamp:
            raise ValueError("risk evaluation time must equal its logical processing time")
        return self


class FillRecord(ExecutionRecord):
    kind: Literal["fill"] = "fill"
    side: OrderSide
    submission: OrderSubmission
    source: MarketDelivery
    assumptions: ExecutionCostConfig
    execution: Fill

    @model_validator(mode="after")
    def causal_fill(self) -> Self:
        command, source, fill = self.submission, self.source, self.execution
        buy = self.side is OrderSide.BUY
        if (self.side is not command.side or fill.quantity != command.quantity
                or fill.action is not (SignalAction.ENTER_LONG if buy else SignalAction.ENTER_SHORT)
                or fill.signal_time != command.timestamp):
            raise ValueError("fill must match the entry submission")
        if (source.sequence <= command.sequence or source.quote.timestamp < command.timestamp
                or self.input_sequence != source.sequence or self.timestamp != source.timestamp
                or fill.execution_time != source.timestamp
                or source.quote.instrument_id != command.instrument_id):
            raise ValueError("fill requires a subsequent causal delivery")
        if (self.assumptions.spread != 0 or fill.spread_adjustment != 0
                or fill.reference_price != (source.quote.ask if buy else source.quote.bid)
                or fill.slippage_adjustment != self.assumptions.slippage):
            raise ValueError("quote execution must use the observed side without synthetic spread")
        # Shared pricing and Fill validators own arithmetic/reconciliation.
        from .pricing import price_quote
        if fill != price_quote(command, source, self.assumptions):
            raise ValueError("fill economics must match recorded assumptions")
        return self


class CancellationOutcome(ExecutionRecord):
    kind: Literal["cancellation"] = "cancellation"
    state: OrderState
    cancelled: bool
    reason: Literal["cancelled", "terminal_order"]

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.cancelled:
            if self.state is not OrderState.CANCELLED or self.reason != "cancelled":
                raise ValueError("successful cancellation must close the accepted order")
        elif self.state not in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED) or self.reason != "terminal_order":
            raise ValueError("denied cancellation must describe a terminal order")
        return self


class OrderActivation(ExecutionRecord):
    kind: Literal["activation"] = "activation"
    submission_id: Identity


class StopTrigger(ExecutionRecord):
    kind: Literal["trigger"] = "trigger"
    source: MarketDelivery
    activation_id: Digest
    stop_price: PositiveDecimal
    side: OrderSide

    @model_validator(mode="after")
    def observed(self) -> Self:
        reference = self.source.quote.ask if self.side is OrderSide.BUY else self.source.quote.bid
        if self.input_sequence != self.source.sequence or self.timestamp != self.source.timestamp:
            raise ValueError("trigger must bind its recorded delivery")
        if not (reference >= self.stop_price if self.side is OrderSide.BUY else reference <= self.stop_price):
            raise ValueError("stop was not crossed")
        return self


class PendingCancellation(ExecutionRecord):
    kind: Literal["cancellation_pending"] = "cancellation_pending"
    request_id: Identity


class AdvancedFillRecord(FillRecord):
    kind: Literal["advanced_fill"] = "advanced_fill"
    submission: AdvancedOrderSubmission
    activation_id: Digest
    trigger_id: Digest | None = None
    cumulative_quantity: PositiveDecimal
    remaining_quantity: NonNegativeDecimal
    liquidity_policy: Literal["full-fill-assumption-v1", "simulated-per-observation-v2"]

    @model_validator(mode="after")
    def causal_fill(self) -> Self:
        from .advanced import validate_fill
        validate_fill(self)
        return self


class OCOFillRecord(AdvancedFillRecord):
    kind: Literal["oco_fill_v3"] = "oco_fill_v3"
    submission: OCOOrderSubmission
    group_id: Digest
    withdrawn_quantity: NonNegativeDecimal

    @model_validator(mode="after")
    def linked(self) -> Self:
        if self.group_id != self.submission.group_id:
            raise ValueError("OCO fill group mismatch")
        return self


class OCOQuantityAdjustment(ExecutionRecord):
    """Withdraw sibling quantity without rewriting a submission or any fill."""
    kind: Literal["oco_quantity_v3"] = "oco_quantity_v3"
    group_id: Digest
    peer_fill: OCOFillRecord
    previous_remaining: PositiveDecimal
    remaining_quantity: NonNegativeDecimal
    withdrawn_quantity: PositiveDecimal

    @model_validator(mode="after")
    def linked(self) -> Self:
        from decimal import localcontext
        from quantlab._decimal import deterministic_context
        f = self.peer_fill
        with localcontext(deterministic_context(prec=4096)):
            if (self.group_id != f.group_id or self.order_id == f.order_id
                    or self.causation_id != f.event_id or self.input_sequence != f.input_sequence
                    or self.timestamp != f.timestamp
                    or self.remaining_quantity != self.previous_remaining - f.execution.quantity
                    or self.withdrawn_quantity < f.execution.quantity):
                raise ValueError("invalid sibling quantity withdrawal")
        return self


PaperEvent = Annotated[OrderTransition | RiskOutcome | FillRecord | AdvancedFillRecord |
    OrderActivation | StopTrigger | PendingCancellation | CancellationOutcome | OCOFillRecord | OCOQuantityAdjustment,
    Field(discriminator="kind")]


class KernelSnapshot(PaperContract):
    config: OCOKernelConfig | AdvancedKernelConfig | KernelConfig
    last_sequence: Annotated[int, Field(ge=0)] = 0
    timestamp: UtcTimestamp | None = None
    market: MarketDelivery | None = None
    submission: OCOOrderSubmission | AdvancedOrderSubmission | OrderSubmission | None = None
    order_id: Digest | None = None
    state: OrderState | None = None
    inputs: tuple[KernelInput, ...] = ()
    events: tuple[PaperEvent, ...] = ()

    @property
    def terminated(self) -> bool:
        return self.state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED)

    @property
    def filled_quantity(self) -> Decimal:
        from decimal import localcontext
        from quantlab._decimal import deterministic_context
        with localcontext(deterministic_context(prec=4096)):
            return sum((e.execution.quantity for e in self.events if isinstance(e, FillRecord)), Decimal("0"))

    @property
    def withdrawn_quantity(self) -> Decimal:
        adjustments = [e for e in self.events if isinstance(e, OCOQuantityAdjustment)]
        return adjustments[-1].withdrawn_quantity if adjustments else Decimal("0")

    @property
    def cumulative_costs(self):
        from decimal import localcontext
        from quantlab._decimal import deterministic_context
        from quantlab.backtesting import CostBreakdown
        with localcontext(deterministic_context(prec=4096)):
            return CostBreakdown(**{name: sum((getattr(e.execution.costs, name)
                for e in self.events if isinstance(e, FillRecord)), Decimal("0"))
                for name in CostBreakdown.model_fields})

    @property
    def remaining_quantity(self) -> Decimal:
        from decimal import localcontext
        from quantlab._decimal import deterministic_context
        with localcontext(deterministic_context(prec=4096)):
            return Decimal("0") if self.submission is None else self.submission.quantity - self.filled_quantity - self.withdrawn_quantity

    @property
    def pending_cancellation(self):
        requests = [e for e in self.events if isinstance(e, PendingCancellation)]
        return requests[-1] if requests and not self.terminated else None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if bool(self.inputs) != (self.timestamp is not None):
            raise ValueError("logical clock requires recorded inputs")
        if self.inputs and (self.last_sequence, self.timestamp) != (
                self.inputs[-1].sequence, self.inputs[-1].timestamp):
            raise ValueError("clock must describe the final input")
        if (self.submission is None) != (self.order_id is None) or (self.order_id is None) != (self.state is None):
            raise ValueError("order identity, submission and state must agree")
        state = None
        fills = 0
        config_digest = stable_id("paper-config-v1", self.config.model_dump(mode="python")) if self.events else None
        for index, event in enumerate(self.events, 1):
            if (event.sequence != index or event.session_id != self.config.session_id
                    or event.config_digest != config_digest
                    or event.order_id != self.order_id or event.input_sequence > self.last_sequence):
                raise ValueError("execution journal metadata is inconsistent")
            if isinstance(event, OrderTransition):
                if event.previous_state is not state:
                    raise ValueError("execution journal transition chain is inconsistent")
                state = event.state
            if isinstance(event, FillRecord):
                fills += 1
        if isinstance(self.config, AdvancedKernelConfig):
            from .advanced import validate_snapshot
            validate_snapshot(self, state)
            return self
        if state is not self.state or fills != int(self.state is OrderState.FILLED):
            raise ValueError("journal must describe the final state and single fill")
        return self


class KernelProgress(PaperContract):
    """V2 audit head bound to an append-only input/event prefix, never authority.

    Complete histories are reconstructed and checked by operational recovery;
    consumers obtain them through KernelSnapshot. This format contains no prefix.
    """
    schema_version: Literal[2] = 2
    config: AdvancedKernelConfig
    last_sequence: Annotated[int, Field(ge=0)]
    timestamp: UtcTimestamp | None
    market: MarketDelivery | None
    submission: AdvancedOrderSubmission | None
    order_id: Digest | None
    state: OrderState | None
    input_count: Annotated[int, Field(ge=0, le=258)]
    event_count: Annotated[int, Field(ge=0)]
    history_digest: Digest
    filled_quantity: NonNegativeDecimal
    cumulative_costs: "CostBreakdown"
    activation: OrderActivation | None
    trigger: StopTrigger | None
    pending_cancellation: PendingCancellation | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if bool(self.input_count) != (self.timestamp is not None):
            raise ValueError("logical clock requires retained inputs")
        if self.input_count > self.config.maximum_inputs + 2:
            raise ValueError("advanced input capacity exceeded")
        if (self.submission is None) != (self.order_id is None) or (self.order_id is None) != (self.state is None):
            raise ValueError("order identity, submission and state must agree")
        if self.submission is None and self.filled_quantity or self.submission is not None and self.filled_quantity > self.submission.quantity:
            raise ValueError("invalid cumulative execution quantity")
        if self.state is OrderState.FILLED and self.filled_quantity != self.submission.quantity:
            raise ValueError("filled order requires complete quantity")
        if self.terminated and self.pending_cancellation is not None:
            raise ValueError("terminal order cannot have pending cancellation")
        return self

    @property
    def terminated(self):
        return self.state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.EXPIRED)


class OCOKernelProgress(KernelProgress):
    schema_version: Literal[3] = 3
    config: OCOKernelConfig
    submission: OCOOrderSubmission | None
    withdrawn_quantity: NonNegativeDecimal

    @model_validator(mode="after")
    def coherent(self) -> Self:
        from decimal import localcontext
        from quantlab._decimal import deterministic_context
        if bool(self.input_count) != (self.timestamp is not None) or self.input_count > self.config.maximum_inputs + 2:
            raise ValueError("invalid OCO clock/capacity")
        if (self.submission is None) != (self.order_id is None) or (self.order_id is None) != (self.state is None):
            raise ValueError("invalid OCO identity")
        with localcontext(deterministic_context(prec=4096)):
            total = self.filled_quantity + self.withdrawn_quantity
            if self.submission is None and total or self.submission is not None and total > self.submission.quantity:
                raise ValueError("OCO quantity exceeds admission")
            if self.state is OrderState.FILLED and total != self.submission.quantity:
                raise ValueError("OCO filled state requires zero outstanding quantity")
        if self.terminated and self.pending_cancellation is not None:
            raise ValueError("terminal OCO child has pending cancellation")
        if self.submission is not None and (self.submission.group_id != self.config.group_id
                or self.submission.position_id != self.config.position_id
                or self.submission.protective_role != self.config.child_role):
            raise ValueError("OCO child attribution mismatch")
        return self

    @property
    def remaining_quantity(self):
        from decimal import localcontext
        from quantlab._decimal import deterministic_context
        with localcontext(deterministic_context(prec=4096)):
            return self.submission.quantity - self.filled_quantity - self.withdrawn_quantity
