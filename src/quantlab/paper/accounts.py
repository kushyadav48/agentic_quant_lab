"""Serialized account ownership with one publication point for owned execution."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, DecimalException, localcontext
from functools import wraps
from itertools import islice
from types import MappingProxyType
from typing import Mapping

from quantlab.backtesting import ExecutionCostConfig
from quantlab.risk import RiskConfig
from .account_models import (
    AccountAdapterRequest, AccountCommand, AccountConfig, AccountEvent, AccountSnapshot,
    ApplyFill, FundReservation, KernelAttribution, MarkAccount, ReleaseFunds, ReserveFunds,
    AdvancedApplyFill, OCOApplyFill, exact_context,
)
from .accounting import initialize_account, transition_account
from .errors import PaperFundingError, PaperIdentityConflict, PaperInputError
from .models import (
    FillRecord, KernelConfig, MarketDelivery, OrderSide, OrderState, OrderTransition, OrderSubmission,
    PaperEvent, canonical_json, stable_id, AdvancedKernelConfig, AdvancedOrderSubmission, AdvancedFillRecord,
    OCOKernelConfig, OCOFillRecord,
)
from .orders import PaperOrderKernel, _KernelState
from .history import RetainedMap
from .strategy_models import OpeningDelivery

MAX_ACCOUNT_EVENTS = 100_000
MAX_KERNELS = 32


@dataclass(frozen=True)
class _OpeningAcknowledgement:
    """Prepared immutable provenance and exact result; never allocated after commit."""
    wire: str
    opening: OpeningDelivery
    source: MarketDelivery
    records: tuple[PaperEvent, ...]


@dataclass(frozen=True)
class _AccountPublication:
    """All publicly visible account/order state is selected by this one pointer."""
    snapshot: AccountSnapshot
    orders: Mapping[str, _KernelState]
    openings: Mapping[tuple[str, str], _OpeningAcknowledgement]
    liquidity: Mapping[str, tuple[Decimal, Decimal]] = field(default_factory=RetainedMap)
    oco: object = None
    entry_gates: Mapping[str, _OpeningAcknowledgement] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class _AccountPreparation:
    before: AccountSnapshot
    after: AccountSnapshot
    item: AccountCommand
    wire: str
    event: AccountEvent
    replayed: bool = False


def _serialized(method):
    """Deny reentrant mutation; no concurrent or distributed service is promised."""
    @wraps(method)
    def call(self, *args, **kwargs):
        if self._busy:
            raise PaperInputError("account transaction already in progress")
        self._busy = True
        try:
            return method(self, *args, **kwargs)
        finally:
            self._busy = False
    return call


class PaperAccount:
    """Trusted local Python owner; no wire surface or arbitrary snapshot restore.

    Account-owned kernel.process() prepares matching and accounting, then publishes
    both together. Independently constructed kernels keep Phase 18A semantics.
    Pure inputs remain trusted Python economics, not authenticated agent payloads.
    """

    def __init__(self, config: AccountConfig):
        self._publication = _AccountPublication(
            initialize_account(config), MappingProxyType({}), MappingProxyType({}))
        self._seen = {}
        self._journal = []
        self._reservation_ids = set()
        self._reserved_orders = set()
        self._settled_orders = set()
        self._execution_ids = set()
        self._kernels = {}
        self._adapter_seen = {}
        self._busy = False
        self._cache_undo = None

    @property
    def snapshot(self) -> AccountSnapshot:
        return self._publication.snapshot

    @property
    def events(self) -> tuple[AccountEvent, ...]:
        # Staged index/journal changes are invisible until publication.
        return tuple(islice(self._journal, self.snapshot.event_sequence))

    def get_input_record(self, event_id: str) -> str:
        prior = self._seen.get(event_id)
        if prior is None or prior[1].after_version > self.snapshot.state_version:
            raise PaperInputError("unknown financial input")
        return prior[0]

    def _owned_state(self, kernel: PaperOrderKernel) -> _KernelState:
        session = kernel._config.session_id
        binding = self._kernels.get(session)
        if binding is None or binding[0] is not kernel:
            raise PaperInputError("kernel is not owned by this account")
        return self._publication.orders[session]

    def _prepare_apply(self, item: AccountCommand) -> _AccountPreparation:
        if type(item) not in (ReserveFunds, ReleaseFunds, ApplyFill, AdvancedApplyFill, OCOApplyFill, MarkAccount):
            raise PaperInputError("expected a strict trusted account input")
        identity = getattr(item, "event_id", None)
        try:
            item = type(item).model_validate(item)
            wire = item.canonical_json()
        except (ValueError, TypeError, DecimalException) as exc:
            if type(identity) is str and (identity in self._seen or identity in self._adapter_seen):
                raise PaperIdentityConflict("retained financial identity reused with invalid content") from exc
            raise PaperInputError("invalid account input") from exc
        before = self.snapshot
        prior = self._seen.get(identity)
        if prior is not None:
            if wire != prior[0]:
                raise PaperIdentityConflict("financial identity reused with different content")
            return _AccountPreparation(before, before, item, wire, prior[1], replayed=True)
        if identity in self._adapter_seen:
            raise PaperIdentityConflict("financial identity collides with an adapter acknowledgment")
        if len(self._seen) >= MAX_ACCOUNT_EVENTS:
            raise PaperInputError("account event retention capacity reached")
        if isinstance(item, ReserveFunds):
            if (item.reservation.reservation_id in self._reservation_ids
                    or item.reservation.order_id in self._reserved_orders):
                raise PaperIdentityConflict("reservation identity/order already admitted")
        if isinstance(item, ApplyFill) and ((not isinstance(item, AdvancedApplyFill) and item.order_id in self._settled_orders)
                or item.causation_id in self._execution_ids):
            raise PaperIdentityConflict("execution transaction already settled")
        updated, event = transition_account(before, item)
        return _AccountPreparation(before, updated, item, wire, event)

    def _stage_indexes(self, financial: tuple[_AccountPreparation, ...]) -> None:
        """Private staging only; public reads still select the old publication."""
        for prepared in financial:
            item = prepared.item
            if self._cache_undo is not None:
                self._cache_undo.append(("_seen", item.event_id))
                if isinstance(item, ReserveFunds):
                    self._cache_undo.extend((("_reservation_ids", item.reservation.reservation_id),
                                             ("_reserved_orders", item.reservation.order_id)))
                if isinstance(item, ApplyFill):
                    if not isinstance(item, AdvancedApplyFill):
                        self._cache_undo.append(("_settled_orders", item.order_id))
                    self._cache_undo.append(("_execution_ids", item.causation_id))
            self._seen[item.event_id] = (prepared.wire, prepared.event)
            self._journal.append(prepared.event)
            if isinstance(item, ReserveFunds):
                self._reservation_ids.add(item.reservation.reservation_id)
                self._reserved_orders.add(item.reservation.order_id)
            if isinstance(item, ApplyFill):
                if not isinstance(item, AdvancedApplyFill):
                    self._settled_orders.add(item.order_id)
                self._execution_ids.add(item.causation_id)

    def _publish(self, before: _AccountPublication, snapshot: AccountSnapshot,
                 orders: Mapping[str, _KernelState], financial=(), adapter_entry=None,
                 opening_entry=None, liquidity_entry=None, oco=None) -> None:
        """Stage reversible caches, then publish all authoritative state once.

        Validation, serialization, pricing and allocation of the new root happen
        before publication. No callable, validation or I/O follows the pointer swap.
        """
        if self._publication is not before:
            raise PaperInputError("stale account preparation")
        if any(p.replayed or p.before is not before.snapshot for p in financial):
            raise PaperInputError("invalid financial preparation")
        openings = before.openings
        if opening_entry is not None:
            key, acknowledgement = opening_entry
            if key in openings:
                raise PaperIdentityConflict("opening acknowledgement already retained")
            # Allocate the candidate retry/provenance index before financial staging.
            openings = MappingProxyType({**openings, key: acknowledgement})
        gates = before.entry_gates
        if opening_entry is not None:
            gates = MappingProxyType({**gates, key[0]: acknowledgement})
        liquidity = before.liquidity
        if liquidity_entry is not None:
            key, budget, quantity = liquidity_entry
            with localcontext(exact_context()):
                prior = liquidity.get(key)
                consumed = (Decimal("0") if prior is None else prior[1]) + quantity
                if (prior is not None and prior[0] != budget) or consumed > budget:
                    raise PaperInputError("shared observation liquidity budget conflict")
                liquidity = liquidity.set(key, (budget, consumed))
        group = before.oco if oco is None else oco
        if group is not None:
            from .oco import validate_publication
            validate_publication(snapshot, orders, group)
        new_root = _AccountPublication(snapshot, MappingProxyType(dict(orders)), openings, liquidity, group, gates)
        journal_length = len(self._journal)
        try:
            self._stage_indexes(financial)
            if adapter_entry is not None:
                identity, wire, event = adapter_entry
                if self._cache_undo is not None:
                    self._cache_undo.append(("_adapter_seen", identity))
                self._adapter_seen[identity] = (wire, event)
            self._publication = new_root
        except BaseException:
            # Even injected staging errors/interrupts expose only the old root.
            object.__setattr__(self, "_publication", before)
            del self._journal[journal_length:]
            for prepared in financial:
                item = prepared.item
                self._seen.pop(item.event_id, None)
                if isinstance(item, ReserveFunds):
                    self._reservation_ids.discard(item.reservation.reservation_id)
                    self._reserved_orders.discard(item.reservation.order_id)
                if isinstance(item, ApplyFill):
                    if not isinstance(item, AdvancedApplyFill):
                        self._settled_orders.discard(item.order_id)
                    self._execution_ids.discard(item.causation_id)
            if adapter_entry is not None:
                self._adapter_seen.pop(adapter_entry[0], None)
            raise

    @_serialized
    def apply_trusted(self, item: AccountCommand) -> AccountEvent:
        """Pure accounting owner; new inputs targeting owned orders use coordination."""
        before = self._publication
        prepared = self._prepare_apply(item)
        if prepared.replayed:
            return prepared.event
        if before.oco is not None and before.oco.head.active and isinstance(prepared.item, ApplyFill):
            raise PaperInputError("active OCO position requires group coordination")
        order_id = (prepared.item.reservation.order_id if isinstance(prepared.item, ReserveFunds)
                    else getattr(prepared.item, "order_id", None))
        if order_id is not None and any(
                state.snapshot.order_id == order_id for state in before.orders.values()):
            raise PaperInputError("owned orders require coordinated accounting")
        self._publish(before, prepared.after, before.orders, (prepared,))
        return prepared.event

    @_serialized
    def create_order_kernel(self, *, session_id: str, strategy_id: str,
                            risk: RiskConfig | None = None,
                            costs: ExecutionCostConfig | None = None) -> PaperOrderKernel:
        try:
            attribution = KernelAttribution(session_id=session_id, strategy_id=strategy_id)
        except (ValueError, TypeError) as exc:
            raise PaperInputError("invalid kernel attribution") from exc
        before = self._publication
        state = before.snapshot
        if (state.position is not None and state.position.quantity > 0
                or session_id in self._kernels or len(self._kernels) >= MAX_KERNELS):
            raise PaperInputError("kernel requires flat account and unused bounded session")
        kernel = PaperOrderKernel(KernelConfig(session_id=session_id,
            instrument=state.config.instrument, flat_equity=state.equity,
            running_peak_equity=state.running_peak_equity,
            risk=RiskConfig() if risk is None else risk,
            costs=ExecutionCostConfig() if costs is None else costs))
        orders = {**before.orders, session_id: kernel._current_state()}
        kernel._bind_account(self)
        self._kernels[session_id] = (kernel, attribution.strategy_id)
        try:
            self._publish(before, state, orders)
        except BaseException:
            self._kernels.pop(session_id)
            raise
        return kernel

    @_serialized
    def create_advanced_order_kernel(self, *, session_id, strategy_id, risk=None, costs=None,
                                     liquidity_per_observation=None, maximum_age=None, reduce_only=False, maximum_inputs=128):
        """Exclusive v2 order; one position/strategy, no netting, hedging or OCO."""
        from datetime import timedelta
        before = self._publication
        position = before.snapshot.position
        active = position is not None and position.quantity > 0
        if (session_id in self._kernels or len(self._kernels) >= MAX_KERNELS or
                any(k._view.state in (OrderState.SUBMITTED, OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)
                    for k, _ in self._kernels.values()) or before.snapshot.reservations or
                reduce_only != active or active and position.strategy_id != strategy_id):
            raise PaperInputError("advanced order requires exclusive matching position ownership")
        try:
            attribution = KernelAttribution(session_id=session_id, strategy_id=strategy_id)
            config = AdvancedKernelConfig(schema_version=2, session_id=session_id, instrument=before.snapshot.config.instrument,
                flat_equity=before.snapshot.equity, running_peak_equity=before.snapshot.running_peak_equity,
                risk=RiskConfig() if risk is None else risk,
                costs=ExecutionCostConfig() if costs is None else costs,
                liquidity_per_observation=liquidity_per_observation,
                maximum_age=timedelta(seconds=60) if maximum_age is None else maximum_age,
                position_id=position.entry_transaction_id if active else None, maximum_inputs=maximum_inputs)
            kernel = PaperOrderKernel(config)
        except (ValueError, TypeError) as exc:
            raise PaperInputError("invalid advanced kernel policy") from exc
        owned = kernel._current_state()
        kernel._bind_account(self)
        self._kernels[session_id] = (kernel, attribution.strategy_id)
        try:
            self._publish(before, before.snapshot, {**before.orders, session_id: owned})
        except BaseException:
            self._kernels.pop(session_id, None)
            raise
        return kernel

    def _reservation_input(self, k, strategy, event_id, sequence, timestamp):
        """Reuse one exact reservation policy for adapters and staged strategy entry."""
        accepted = k.accepted
        try:
            with localcontext(exact_context()):
                buy = k.submission.side is OrderSide.BUY
                reference = k.market.quote.ask if buy else k.market.quote.bid
                price = (reference + k.config.costs.slippage if buy
                         else reference - k.config.costs.slippage)
                if price <= 0:
                    raise ValueError("nonpositive reservation price")
                fee_count = 1
                if isinstance(k.submission, AdvancedOrderSubmission):
                    if k.submission.reduce_only:
                        raise PaperInputError("position reductions do not reserve entry collateral")
                    if k.submission.limit_price is not None:
                        price = k.submission.limit_price
                    if k.config.liquidity_per_observation is not None:
                        fee_count = k.submission.quantity / k.config.instrument.quantity_increment
                amount = (price * k.submission.quantity
                    + k.config.costs.commission_per_unit * k.submission.quantity
                    + k.config.costs.fixed_fee_per_fill * fee_count)
        except (ValueError, DecimalException) as exc:
            raise PaperInputError("reservation economics cannot be represented") from exc
        reservation = FundReservation(reservation_id=self._reservation_id(k),
            account_id=self.snapshot.config.account_id, strategy_id=strategy,
            order_id=k.order_id, accepted_event_id=accepted.event_id, amount=amount)
        return ReserveFunds(event_id=event_id, account_id=self.snapshot.config.account_id,
            strategy_id=strategy, sequence=sequence, timestamp=timestamp,
            transaction_id=k.order_id, causation_id=accepted.event_id, reservation=reservation)

    @_serialized
    def _submit_strategy_entry(self, kernel, source, command):
        """Private Phase 18C batch: quote, entry and reservation publish together.

        A standalone staging kernel reuses exact Phase 18A risk/order semantics.
        It cannot fill, and only the account publishes authoritative owned state.
        """
        owned = self._owned_state(kernel)
        before = self._publication
        if owned.snapshot.submission is not None:
            # Both identities must exactly match; replay never re-reserves money.
            market_retry = kernel._prepare(source)
            command_retry = kernel._prepare(command)
            if not market_retry.replayed or not command_retry.replayed:
                raise PaperInputError("strategy entry batch already consumed")
            return command_retry.records
        if owned.snapshot.inputs or source.timestamp < before.snapshot.timestamp:
            raise PaperInputError("strategy entry requires a fresh causal owned kernel")
        staged = PaperOrderKernel(owned.snapshot.config)
        staged.process(source)
        records = staged.process(command)
        proposed = staged._current_state()
        financial = ()
        if proposed.snapshot.state is OrderState.ACCEPTED:
            strategy = self._kernels[proposed.snapshot.config.session_id][1]
            item = self._reservation_input(proposed.snapshot, strategy,
                stable_id("paper-strategy-reserve-v1", command.command_id),
                before.snapshot.last_input_sequence + 1, command.timestamp)
            financial = (self._prepare_apply(item),)
        snapshot = financial[0].after if financial else before.snapshot
        self._publish(before, snapshot,
            {**before.orders, proposed.snapshot.config.session_id: proposed}, financial)
        return records

    def _reservation_id(self, state) -> str:
        return stable_id("paper-reservation-v1",
            (self.snapshot.config.account_id, state.config.session_id, state.order_id))

    def _remaining_liquidity(self, source, budget):
        key = stable_id("paper-simulated-observation-v2", source.quote)
        prior = self._publication.liquidity.get(key)
        with localcontext(exact_context()):
            if prior is not None and prior[0] != budget:
                raise PaperInputError("shared quote cannot change liquidity policy")
            if budget is None:
                return None
            return budget - (Decimal("0") if prior is None else prior[1])

    def _execution_input(self, state, fill: FillRecord) -> ApplyFill:
        cls = OCOApplyFill if isinstance(fill, OCOFillRecord) else AdvancedApplyFill if isinstance(fill, AdvancedFillRecord) else ApplyFill
        extra = {} if cls is ApplyFill else dict(original_quantity=fill.submission.quantity,
            cumulative_quantity=fill.cumulative_quantity, remaining_quantity=fill.remaining_quantity,
            release_remaining=state.state in (OrderState.EXPIRED, OrderState.CANCELLED))
        if isinstance(fill, OCOFillRecord):
            with localcontext(exact_context()):
                extra.update(group_id=fill.group_id, position_id=fill.submission.position_id, own_cumulative_quantity=fill.cumulative_quantity,
                    withdrawn_quantity=fill.withdrawn_quantity,
                    cumulative_quantity=fill.cumulative_quantity + fill.withdrawn_quantity)
        assumptions = fill.assumptions
        if isinstance(fill, AdvancedFillRecord):
            from .advanced import effective_costs
            assumptions = effective_costs(fill.submission, fill.source, fill.assumptions)
        return cls(event_id=stable_id("paper-account-settlement-v1",
                (self.snapshot.config.account_id, fill.event_id)),
            account_id=self.snapshot.config.account_id,
            strategy_id=self._kernels[state.config.session_id][1],
            sequence=self.snapshot.last_input_sequence + 1, timestamp=fill.timestamp,
            transaction_id=state.order_id, causation_id=fill.event_id, order_id=state.order_id,
            reservation_id=None if isinstance(fill, AdvancedFillRecord) and fill.submission.reduce_only else self._reservation_id(state),
            execution=fill.execution, source=fill.source, assumptions=assumptions, **extra)

    def _release_input(self, state, terminal) -> ReleaseFunds:
        return ReleaseFunds(event_id=stable_id("paper-account-release-v1",
                (self.snapshot.config.account_id, terminal.event_id)),
            account_id=self.snapshot.config.account_id,
            strategy_id=self._kernels[state.config.session_id][1],
            sequence=self.snapshot.last_input_sequence + 1, timestamp=terminal.timestamp,
            transaction_id=state.order_id, causation_id=terminal.event_id,
            reservation_id=self._reservation_id(state), order_id=state.order_id,
            reason="cancelled" if state.state in (OrderState.CANCELLED, OrderState.EXPIRED) else "rejected")

    def _opening_acknowledgement(self, kernel, event_id):
        self._owned_state(kernel)
        return self._publication.openings.get((kernel._config.session_id, event_id))

    def _opening_records(self, kernel):
        self._owned_state(kernel)
        session = kernel._config.session_id
        return tuple(ack.opening for (owner, _), ack in self._publication.openings.items()
                     if owner == session)

    def _prepare_opening_acknowledgement(self, opening, source, records):
        """All acknowledgement allocation and canonical checks precede publication."""
        wire = opening.canonical_json()
        canonical_json((opening, source, records))
        return _OpeningAcknowledgement(wire, opening, source, records)

    @_serialized
    def _process_strategy_open(self, kernel, opening):
        """Private strategy path: provenance/retry state commits with owned execution."""
        if type(opening) is not OpeningDelivery:
            raise PaperInputError("expected a canonical opening delivery")
        opening = OpeningDelivery.model_validate(opening)
        wire = opening.canonical_json()
        prior = self._opening_acknowledgement(kernel, opening.event_id)
        if prior is not None:
            if prior.wire != wire:
                raise PaperIdentityConflict("opening identity reused with different content")
            return prior.records
        source = MarketDelivery(event_id=stable_id("paper-strategy-opening-v1", opening),
            sequence=opening.sequence, timestamp=opening.timestamp,
            delivered_at=opening.delivered_at, quote=opening.quote)
        return self._process_order(kernel, source, opening=opening)

    @_serialized
    def process_order(self, kernel: PaperOrderKernel, item):
        """Single deterministic preparation/commit boundary for an owned kernel."""
        return self._process_order(kernel, item)

    def _process_order(self, kernel, item, *, opening=None):
        if type(kernel) is not PaperOrderKernel:
            raise PaperInputError("expected an owned kernel")
        self._owned_state(kernel)
        if isinstance(kernel._config, OCOKernelConfig):
            raise PaperInputError("linked children require OCO coordination")
        before = self._publication
        proposed = kernel._prepare(item)
        if proposed.replayed:
            if opening is not None:
                raise PaperInputError("opening execution lacks its coordinated acknowledgement")
            return proposed.records
        if isinstance(item, OrderSubmission):
            for session, (other, _) in self._kernels.items():
                if other is not kernel and other._view.state in (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED):
                    if isinstance(item, AdvancedOrderSubmission) or isinstance(other._config, AdvancedKernelConfig):
                        raise PaperInputError("advanced execution requires one active account-owned order")
        if isinstance(item, AdvancedOrderSubmission) and item.reduce_only:
            position = before.snapshot.position
            if (position is None or position.quantity == 0 or item.position_id != position.entry_transaction_id or
                    self._kernels[kernel._config.session_id][1] != position.strategy_id or
                    item.quantity > position.quantity or item.timestamp < position.entry_time or
                    item.side is not (OrderSide.SELL if position.direction.value == "long" else OrderSide.BUY)):
                raise PaperInputError("invalid reduce-only quantity, direction or position ownership")
        if proposed.after.snapshot.timestamp < before.snapshot.timestamp:
            raise PaperInputError("owned order input precedes the account clock")
        financial = ()
        fills = [e for e in proposed.records if isinstance(e, FillRecord)]
        if fills:
            try:
                prepared = self._prepare_apply(self._execution_input(proposed.after.snapshot, fills[0]))
            except PaperIdentityConflict:
                raise
            except PaperInputError as exc:
                proposed = kernel._without_fill(proposed,
                    "account_unfunded" if isinstance(exc, PaperFundingError) else "account_rejected")
            else:
                if prepared.replayed:
                    raise PaperIdentityConflict("proposed execution was already accounted")
                financial = (prepared,)
        state = proposed.after.snapshot
        if state.state in (OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED) and not financial:
            reservation = next((r for r in before.snapshot.reservations
                if r.order_id == state.order_id), None)
            if reservation is not None:
                terminal = state.transition
                financial = (self._prepare_apply(self._release_input(state, terminal)),)
        snapshot = financial[0].after if financial else before.snapshot
        orders = {**before.orders, state.config.session_id: proposed.after}
        opening_entry = None
        if opening is not None:
            acknowledgement = self._prepare_opening_acknowledgement(
                opening, item, proposed.records)
            opening_entry = ((state.config.session_id, opening.event_id), acknowledgement)
        liquidity_entry = None
        committed_fill = next((e for e in proposed.records if isinstance(e, AdvancedFillRecord)), None)
        if committed_fill is not None and state.config.liquidity_per_observation is not None:
            liquidity_entry = (stable_id("paper-simulated-observation-v2", committed_fill.source.quote),
                state.config.liquidity_per_observation, committed_fill.execution.quantity)
        self._publish(before, snapshot, orders, financial, opening_entry=opening_entry,
            liquidity_entry=liquidity_entry)
        return proposed.records

    @_serialized
    def _adapt(self, operation, kernel: PaperOrderKernel, *, event_id: str, sequence: int,
               timestamp: datetime) -> AccountEvent:
        if type(kernel) is not PaperOrderKernel:
            raise PaperInputError("expected an owned kernel")
        k = self._owned_state(kernel).snapshot
        if isinstance(k.config, OCOKernelConfig):
            raise PaperInputError("linked children do not support independent adapters")
        session = k.config.session_id
        strategy = self._kernels[session][1]
        try:
            request = AccountAdapterRequest(operation=operation, session_id=session,
                event_id=event_id, sequence=sequence, timestamp=timestamp)
            wire = stable_id("paper-account-adapter-v1", request)
            timestamp = request.timestamp
        except (ValueError, TypeError) as exc:
            if type(event_id) is str and (event_id in self._seen or event_id in self._adapter_seen):
                raise PaperIdentityConflict("adapter identity reused with invalid request") from exc
            raise PaperInputError("invalid account adapter request") from exc
        retry = self._adapter_seen.get(event_id)
        if retry is not None:
            if retry[0] != wire:
                raise PaperIdentityConflict("adapter identity reused with different request")
            return retry[1]
        if len(self._adapter_seen) >= MAX_ACCOUNT_EVENTS:
            raise PaperInputError("adapter acknowledgment capacity reached")
        before = self._publication
        if k.order_id is None or k.timestamp > timestamp:
            raise PaperInputError("adapter requires a causal retained order")
        if operation == "reserve":
            if k.state is not OrderState.ACCEPTED:
                raise PaperInputError("reservation requires an accepted order")
            item = self._reservation_input(k, strategy, event_id, sequence, timestamp)
            prepared = self._prepare_apply(item)
            self._publish(before, prepared.after, before.orders, (prepared,),
                (event_id, wire, prepared.event))
            return prepared.event
        # Backward compatible terminal methods acknowledge automatic accounting.
        if event_id in self._seen:
            raise PaperIdentityConflict("acknowledgment identity collides with a financial input")
        if operation == "release":
            if k.state not in (OrderState.CANCELLED, OrderState.REJECTED):
                raise PaperInputError("release requires cancellation/rejection")
            terminal = k.transition
            identity = self._release_input(k, terminal).event_id
        else:
            if k.state is not OrderState.FILLED:
                raise PaperInputError("settlement requires a retained filled order")
            fill = k.first_fill
            if timestamp != fill.timestamp:
                raise PaperInputError("settlement uses the execution's logical time")
            identity = self._execution_input(k, fill).event_id
        prior = self._seen.get(identity)
        if prior is None:
            raise PaperInputError("no committed terminal accounting application")
        self._publish(before, before.snapshot, before.orders,
            adapter_entry=(event_id, wire, prior[1]))
        return prior[1]

    def reserve_order(self, kernel, *, event_id, sequence, timestamp):
        return self._adapt("reserve", kernel, event_id=event_id, sequence=sequence, timestamp=timestamp)

    def release_order(self, kernel, *, event_id, sequence, timestamp):
        return self._adapt("release", kernel, event_id=event_id, sequence=sequence, timestamp=timestamp)

    def settle_order(self, kernel, *, event_id, sequence, timestamp):
        return self._adapt("settle", kernel, event_id=event_id, sequence=sequence, timestamp=timestamp)

    @property
    def oco_progress(self):
        return None if self._publication.oco is None else self._publication.oco.head

    @property
    def oco_snapshot(self):
        if self._publication.oco is None:
            return None
        from .oco_models import OCOSnapshot
        h = self._publication.oco.head
        return OCOSnapshot(group=h, stop=self._kernels[h.stop.config.session_id][0].snapshot,
            target=self._kernels[h.target.config.session_id][0].snapshot)

    @_serialized
    def create_protective_oco(self, command, *, risk=None, costs=None,
                              liquidity_per_observation=None, maximum_age=None, maximum_inputs=128):
        from .oco import create_group
        return create_group(self, command, risk=risk, costs=costs,
            liquidity_per_observation=liquidity_per_observation,
            maximum_age=maximum_age, maximum_inputs=maximum_inputs)

    @_serialized
    def process_oco(self, group_id, item):
        from .oco import process_group
        return process_group(self, group_id, item)

    def mark(self, item: MarkAccount) -> AccountEvent:
        if type(item) is not MarkAccount:
            raise PaperInputError("expected an available market valuation")
        return self.apply_trusted(item)
