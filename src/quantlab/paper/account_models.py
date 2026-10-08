"""Immutable v1 fully prefunded research P&L contracts, not brokerage cash."""
from decimal import Decimal, Inexact, localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from quantlab._decimal import deterministic_context
from quantlab.backtesting import ExecutionCostConfig, Fill, PositionSide
from quantlab.data import AssetClass, Instrument
from quantlab.data.models import CurrencyCode, NonNegativeDecimal, PositiveDecimal, UtcTimestamp
from quantlab.risk.models import FiniteDecimal
from quantlab.strategies.schema import Digest
from .models import Identity, LogicalInput, MarketDelivery, PaperContract, stable_id

ZERO = Decimal("0")


def exact_context():
    """Bounded exact arithmetic; reject inexact results instead of rounding money."""
    context = deterministic_context(prec=4096)
    context.traps[Inexact] = True
    return context


class AccountConfig(PaperContract):
    account_id: Identity
    denomination: CurrencyCode
    instrument: Instrument
    starting_capital: PositiveDecimal
    timestamp: UtcTimestamp
    policy: Literal["prefunded-linear-equity-pnl-v1"] = "prefunded-linear-equity-pnl-v1"

    @model_validator(mode="after")
    def supported(self) -> Self:
        if (self.instrument.asset_class is not AssetClass.EQUITY
                or self.instrument.contract_multiplier != 1
                or self.denomination != self.instrument.quote_currency
                or self.instrument.base_currency is not None):
            raise ValueError("v1 supports equity research units, quote denomination and multiplier 1 only")
        self.canonical_json()
        return self


class FundReservation(PaperContract):
    reservation_id: Identity
    account_id: Identity
    strategy_id: Identity
    order_id: Digest
    accepted_event_id: Digest
    amount: PositiveDecimal


class AccountPosition(PaperContract):
    account_id: Identity
    strategy_id: Identity
    instrument: Instrument
    direction: PositionSide
    quantity: NonNegativeDecimal
    entry_basis: PositiveDecimal
    cost_basis: NonNegativeDecimal
    realized_pnl: FiniteDecimal = ZERO
    unrealized_pnl: FiniteDecimal = ZERO
    fees_paid: NonNegativeDecimal = ZERO
    entry_transaction_id: Identity
    entry_time: UtcTimestamp
    valuation: MarketDelivery

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        with localcontext(exact_context()):
            if self.cost_basis != self.entry_basis * self.quantity:
                raise ValueError("position basis must reconcile")
            if (self.instrument.contract_multiplier != 1
                    or self.instrument.asset_class is not AssetClass.EQUITY
                    or self.instrument.base_currency is not None
                    or self.valuation.quote.instrument_id != self.instrument.instrument_id
                    or self.valuation.timestamp < self.entry_time):
                raise ValueError("invalid position instrument or valuation provenance")
            n, d = self.quantity.as_integer_ratio()
            sn, sd = self.instrument.quantity_increment.as_integer_ratio()
            if (n * sd) % (d * sn):
                raise ValueError("position quantity increment mismatch")
            mark = self.valuation.quote.bid if self.direction is PositionSide.LONG else self.valuation.quote.ask
            gross = (mark - self.entry_basis if self.direction is PositionSide.LONG
                     else self.entry_basis - mark) * self.quantity
            if gross != self.unrealized_pnl:
                raise ValueError("position liquidation P&L must reconcile")
        return self


class AccountSnapshot(PaperContract):
    config: AccountConfig
    balance: FiniteDecimal
    available_funds: NonNegativeDecimal
    reserved_funds: NonNegativeDecimal = ZERO
    position_collateral: NonNegativeDecimal = ZERO
    realized_pnl: FiniteDecimal = ZERO
    unrealized_pnl: FiniteDecimal = ZERO
    fees_paid: NonNegativeDecimal = ZERO
    equity: FiniteDecimal
    running_peak_equity: PositiveDecimal
    position: AccountPosition | None = None
    reservations: tuple[FundReservation, ...] = ()
    state_version: Annotated[int, Field(ge=0)] = 0
    event_sequence: Annotated[int, Field(ge=0)] = 0
    last_input_sequence: Annotated[int, Field(ge=0)] = 0
    timestamp: UtcTimestamp
    last_event_id: Digest

    @property
    def net_realized_pnl(self) -> Decimal:
        with localcontext(exact_context()):
            return self.realized_pnl - self.fees_paid

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        with localcontext(exact_context()):
            ids = [r.reservation_id for r in self.reservations]
            orders = [r.order_id for r in self.reservations]
            if len(ids) != len(set(ids)) or len(orders) != len(set(orders)):
                raise ValueError("duplicate reservation identity/order")
            if any(r.account_id != self.config.account_id for r in self.reservations):
                raise ValueError("reservation ownership mismatch")
            if self.reserved_funds != sum((r.amount for r in self.reservations), ZERO):
                raise ValueError("reservation total mismatch")
            collateral, unrealized = ZERO, ZERO
            if self.position is not None:
                p = self.position
                if p.account_id != self.config.account_id or p.instrument != self.config.instrument:
                    raise ValueError("position ownership mismatch")
                if (p.valuation.timestamp > self.timestamp
                        or not self.config.timestamp <= p.entry_time <= self.timestamp
                        or p.fees_paid > self.fees_paid):
                    raise ValueError("future valuation")
                collateral, unrealized = p.cost_basis, p.unrealized_pnl
            if (self.position_collateral != collateral or self.unrealized_pnl != unrealized
                    or self.balance != self.config.starting_capital + self.realized_pnl - self.fees_paid
                    or self.equity != self.balance + self.unrealized_pnl
                    or self.available_funds != self.balance - self.reserved_funds - collateral):
                raise ValueError("account financial invariants do not reconcile")
            if self.running_peak_equity < max(self.config.starting_capital, self.equity):
                raise ValueError("peak equity mismatch")
            if (self.state_version != self.event_sequence
                    or self.timestamp < self.config.timestamp
                    or bool(self.state_version) != bool(self.last_input_sequence)):
                raise ValueError("account clock/version mismatch")
        return self


class AccountInput(LogicalInput):
    event_id: Identity
    account_id: Identity
    strategy_id: Identity
    transaction_id: Identity
    causation_id: Identity


class ReserveFunds(AccountInput):
    kind: Literal["reserve"] = "reserve"
    reservation: FundReservation


class ReleaseFunds(AccountInput):
    kind: Literal["release"] = "release"
    reservation_id: Identity
    order_id: Digest
    reason: Literal["cancelled", "rejected"]


class ApplyFill(AccountInput):
    """Trusted Python execution economics; never a wire/agent execution command."""
    kind: Literal["settle"] = "settle"
    order_id: Digest
    reservation_id: Identity | None = None
    execution: Fill
    source: MarketDelivery
    assumptions: ExecutionCostConfig


class MarkAccount(AccountInput):
    kind: Literal["mark"] = "mark"
    source: MarketDelivery


class AccountEvent(PaperContract):
    event_id: Digest
    account_id: Identity
    strategy_id: Identity
    transaction_id: Identity
    causation_id: Identity
    input_id: Identity
    input_digest: Digest
    policy: Literal["prefunded-linear-equity-pnl-v1"]
    kind: Literal["reserve", "release", "settle", "mark"]
    sequence: Annotated[int, Field(gt=0)]
    input_sequence: Annotated[int, Field(gt=0)]
    timestamp: UtcTimestamp
    before_version: Annotated[int, Field(ge=0)]
    after_version: Annotated[int, Field(gt=0)]
    previous_event_id: Digest
    balance: FiniteDecimal
    equity: FiniteDecimal
    available_funds: NonNegativeDecimal
    reserved_funds: NonNegativeDecimal
    position_collateral: NonNegativeDecimal
    realized_pnl: FiniteDecimal
    unrealized_pnl: FiniteDecimal
    fees_paid: NonNegativeDecimal

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.after_version != self.before_version + 1
                or self.sequence != self.after_version
                or self.event_id != stable_id("paper-account-event-v1",
                    self.model_dump(mode="python", exclude={"event_id"}))):
            raise ValueError("account event identity/version mismatch")
        return self


class KernelAttribution(PaperContract):
    session_id: Identity
    strategy_id: Identity


class AccountAdapterRequest(LogicalInput):
    event_id: Identity
    operation: Literal["reserve", "release", "settle"]
    session_id: Identity


AccountCommand = ReserveFunds | ReleaseFunds | ApplyFill | MarkAccount
