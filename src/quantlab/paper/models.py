"""Frozen offline order contracts; no strategy admission or cash semantics."""
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from quantlab.backtesting import ExecutionCostConfig, Fill, SignalAction
from quantlab.data import Instrument, MarketQuote
from quantlab.data.models import PositiveDecimal, UtcTimestamp, _DomainModel
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


class CancellationRequest(LogicalInput):
    kind: Literal["cancel"] = "cancel"
    command_id: Identity
    causation_id: Identity
    order_id: Digest


KernelInput = Annotated[MarketDelivery | OrderSubmission | CancellationRequest,
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
    "account_unfunded", "account_rejected"]


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
            OrderState.ACCEPTED: (OrderState.CANCELLED, OrderState.FILLED),
        }
        if self.state not in legal.get(self.previous_state, ()):
            raise ValueError("illegal order transition")
        if self.state is OrderState.REJECTED:
            if self.reason not in ("risk_rejected", "risk_error"):
                raise ValueError("rejection requires a risk reason")
        elif self.state is OrderState.CANCELLED:
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
        elif self.state not in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED) or self.reason != "terminal_order":
            raise ValueError("denied cancellation must describe a terminal order")
        return self


PaperEvent = Annotated[OrderTransition | RiskOutcome | FillRecord | CancellationOutcome,
    Field(discriminator="kind")]


class KernelSnapshot(PaperContract):
    config: KernelConfig
    last_sequence: Annotated[int, Field(ge=0)] = 0
    timestamp: UtcTimestamp | None = None
    market: MarketDelivery | None = None
    submission: OrderSubmission | None = None
    order_id: Digest | None = None
    state: OrderState | None = None
    inputs: tuple[KernelInput, ...] = ()
    events: tuple[PaperEvent, ...] = ()

    @property
    def terminated(self) -> bool:
        return self.state in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELLED)

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
        for index, event in enumerate(self.events, 1):
            if (event.sequence != index or event.session_id != self.config.session_id
                    or event.config_digest != stable_id("paper-config-v1", self.config.model_dump(mode="python"))
                    or event.order_id != self.order_id or event.input_sequence > self.last_sequence):
                raise ValueError("execution journal metadata is inconsistent")
            if isinstance(event, OrderTransition):
                if event.previous_state is not state:
                    raise ValueError("execution journal transition chain is inconsistent")
                state = event.state
            if isinstance(event, FillRecord):
                fills += 1
        if state is not self.state or fills != int(self.state is OrderState.FILLED):
            raise ValueError("journal must describe the final state and single fill")
        return self
