"""Pure bounded accounting transitions. Inputs here are trusted Python records."""
from decimal import DecimalException, localcontext

from quantlab.backtesting import PositionSide, SignalAction
from quantlab.backtesting.execution import price_execution
from .account_models import (
    AccountCommand, AccountConfig, AccountEvent, AccountPosition, AccountSnapshot, ApplyFill,
    AdvancedApplyFill, OCOApplyFill, FundReservation, MarkAccount, ReleaseFunds, ReserveFunds, ZERO, exact_context,
)
from .errors import PaperFundingError, PaperInputError
from .models import stable_id

MAX_RESERVATIONS = 32


def initialize_account(config: AccountConfig) -> AccountSnapshot:
    try:
        config = AccountConfig.model_validate(config)
        capital = config.starting_capital
        return AccountSnapshot(config=config, balance=capital, equity=capital,
            available_funds=capital, running_peak_equity=capital, timestamp=config.timestamp,
            last_event_id=stable_id("paper-account-initial-v1", config))
    except (ValueError, TypeError, DecimalException) as exc:
        raise PaperInputError("invalid account configuration") from exc


def _valuation(state, item, position):
    source = item.source
    if (source.quote.instrument_id != state.config.instrument.instrument_id
            or source.timestamp > item.timestamp):
        raise ValueError("valuation must be available for this instrument")
    if position is not None:
        prior = position.valuation
        if (source.timestamp < position.entry_time
                or source.sequence <= prior.sequence
                or source.timestamp < prior.timestamp
                or source.quote.timestamp < prior.quote.timestamp
                or source.delivered_at < prior.delivered_at
                or source.quote.available_at < prior.quote.available_at):
            raise ValueError("valuation chronology cannot go backwards")
    return source


def transition_account(state: AccountSnapshot, item: AccountCommand) -> tuple[AccountSnapshot, AccountEvent]:
    """Pure economics, not execution permission, authentication or retry storage.

    The owning ledger supplies event identity retention and transaction uniqueness.
    A caller cannot replace its authoritative state with the returned snapshot.
    """
    try:
        if type(item) not in (ReserveFunds, ReleaseFunds, ApplyFill, AdvancedApplyFill, OCOApplyFill, MarkAccount):
            raise ValueError("expected a strict account input")
        state = AccountSnapshot.model_validate(state)
        item = type(item).model_validate(item)
        item.canonical_json()
        if (item.account_id != state.config.account_id
                or item.sequence <= state.last_input_sequence
                or item.timestamp < state.timestamp):
            raise ValueError("account ownership/chronology mismatch")
        with localcontext(exact_context()):
            reservations = state.reservations
            position = state.position
            realized, fees = state.realized_pnl, state.fees_paid
            active = position is not None and position.quantity > 0
            if isinstance(item, ReserveFunds):
                r = item.reservation
                if (r.account_id != item.account_id or r.strategy_id != item.strategy_id
                        or r.accepted_event_id != item.causation_id
                        or active or len(reservations) >= MAX_RESERVATIONS
                        or any(x.reservation_id == r.reservation_id or x.order_id == r.order_id
                               for x in reservations)):
                    raise ValueError("invalid reservation identity/ownership or open exposure")
                if r.amount > state.available_funds:
                    raise PaperFundingError("insufficient available funds")
                reservations = (*reservations, r)
            elif isinstance(item, ReleaseFunds):
                matches = [r for r in reservations if r.reservation_id == item.reservation_id]
                if (not matches or matches[0].strategy_id != item.strategy_id
                        or matches[0].order_id != item.order_id):
                    raise ValueError("missing or conflicting reservation")
                reservations = tuple(r for r in reservations if r.reservation_id != item.reservation_id)
            elif isinstance(item, MarkAccount):
                if not active or position.strategy_id != item.strategy_id:
                    raise ValueError("mark requires the owned active position")
                source = _valuation(state, item, position)
                position = _marked(position, source)
            else:
                fill = item.execution
                entry = fill.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)
                long = fill.action in (SignalAction.ENTER_LONG, SignalAction.EXIT_LONG)
                buy = fill.action in (SignalAction.ENTER_LONG, SignalAction.EXIT_SHORT)
                source = _valuation(state, item, position if active else None)
                if (fill.execution_time != source.timestamp or fill.execution_time != item.timestamp
                        or source.quote.timestamp < fill.signal_time
                        or item.assumptions.spread != 0):
                    raise ValueError("fill requires causal quote execution")
                n, d = fill.quantity.as_integer_ratio()
                sn, sd = state.config.instrument.quantity_increment.as_integer_ratio()
                if (n * sd) % (d * sn):
                    raise ValueError("quantity increment mismatch")
                reference = source.quote.ask if buy else source.quote.bid
                price, costs = price_execution(reference, fill.quantity, item.assumptions,
                    buy=buy, spread_adjustment=ZERO)
                if (fill.reference_price != reference or fill.execution_price != price
                        or fill.costs != costs or fill.spread_adjustment != ZERO
                        or fill.slippage_adjustment != item.assumptions.slippage):
                    raise ValueError("fill prices/costs must match trusted quote and policy")
                exact_price = (reference + item.assumptions.slippage if buy
                               else reference - item.assumptions.slippage)
                if (fill.execution_price != exact_price
                        or fill.costs.slippage_cost != item.assumptions.slippage * fill.quantity
                        or fill.costs.commission != item.assumptions.commission_per_unit * fill.quantity
                        or fill.costs.fees != item.assumptions.fixed_fee_per_fill):
                    raise ValueError("rounded execution economics unsupported")
                # Reject 18A rounded monetary fees if they are not exact under v1.
                explicit = item.assumptions.commission_per_unit * fill.quantity + item.assumptions.fixed_fee_per_fill
                if explicit != fill.costs.commission + fill.costs.fees:
                    raise ValueError("rounded explicit costs unsupported")
                fees += explicit
                if entry:
                    continuation = active and isinstance(item, AdvancedApplyFill)
                    if (active and not continuation) or item.reservation_id is None:
                        raise ValueError("entry requires flat ownership and a reservation")
                    if continuation and (position.strategy_id != item.strategy_id or
                            position.entry_transaction_id != item.transaction_id or
                            position.direction is not (PositionSide.LONG if long else PositionSide.SHORT) or
                            item.cumulative_quantity != position.quantity + fill.quantity):
                        raise ValueError("partial entry must continue the exact owned entry")
                    if isinstance(item, AdvancedApplyFill) and not continuation and item.cumulative_quantity != fill.quantity:
                        raise ValueError("first partial entry quantity mismatch")
                    matches = [r for r in reservations if r.reservation_id == item.reservation_id]
                    if (not matches or matches[0].strategy_id != item.strategy_id
                            or matches[0].order_id != item.order_id):
                        raise ValueError("fill reservation ownership mismatch")
                    retained = matches[0]
                    reservations = tuple(r for r in reservations if r.reservation_id != item.reservation_id)
                    if isinstance(item, AdvancedApplyFill) and item.remaining_quantity > 0 and not item.release_remaining:
                        # Keep all unconsumed collateral, including the conservative
                        # per-execution fee allowance, until completion/cancellation.
                        remaining_amount = retained.amount - price * fill.quantity - explicit
                        if remaining_amount <= 0:
                            raise PaperFundingError("partial fill would consume remaining collateral")
                        reservations = (*reservations, FundReservation(**{
                            **retained.model_dump(), "amount": remaining_amount}))
                    direction = PositionSide.LONG if long else PositionSide.SHORT
                    quantity = fill.quantity + (position.quantity if continuation else ZERO)
                    basis_cost = price * fill.quantity + (position.cost_basis if continuation else ZERO)
                    basis = basis_cost / quantity
                    entry_time = position.entry_time if continuation else item.timestamp
                    entry_fees = explicit + (position.fees_paid if continuation else ZERO)
                    mark = source.quote.bid if long else source.quote.ask
                    unrealized = (mark - basis if long else basis - mark) * quantity
                    position = AccountPosition(account_id=item.account_id,
                        strategy_id=item.strategy_id, instrument=state.config.instrument,
                        direction=direction, quantity=quantity, entry_basis=basis,
                        cost_basis=basis_cost, unrealized_pnl=unrealized,
                        fees_paid=entry_fees, entry_transaction_id=item.transaction_id,
                        entry_time=entry_time, valuation=source)
                else:
                    if (not active or item.reservation_id is not None
                            or position.strategy_id != item.strategy_id
                            or position.direction is not (PositionSide.LONG if long else PositionSide.SHORT)
                            or fill.quantity > position.quantity
                            or fill.signal_time < position.entry_time):
                        raise ValueError("unsupported reversal, reduction or ownership")
                    if isinstance(item, OCOApplyFill) and (position.entry_transaction_id != item.position_id
                            or position.quantity - fill.quantity != item.remaining_quantity):
                        raise ValueError("OCO settlement ignores the live position")
                    gross = (price - position.entry_basis if long else position.entry_basis - price) * fill.quantity
                    realized += gross
                    quantity = position.quantity - fill.quantity
                    values = position.model_dump(mode="python")
                    values.update(quantity=quantity, cost_basis=position.entry_basis * quantity,
                        realized_pnl=position.realized_pnl + gross,
                        fees_paid=position.fees_paid + explicit)
                    mark = source.quote.bid if long else source.quote.ask
                    values.update(valuation=source, unrealized_pnl=(
                        mark - position.entry_basis if long else position.entry_basis - mark) * quantity)
                    position = AccountPosition(**values)

            reserved = sum((r.amount for r in reservations), ZERO)
            collateral = position.cost_basis if position is not None else ZERO
            unrealized = position.unrealized_pnl if position is not None else ZERO
            balance = state.config.starting_capital + realized - fees
            available = balance - reserved - collateral
            equity = balance + unrealized
            if available < 0:
                raise PaperFundingError("settlement exceeds prefunded capacity")
            version = state.state_version + 1
            values = dict(balance=balance, available_funds=available, reserved_funds=reserved,
                position_collateral=collateral, realized_pnl=realized, unrealized_pnl=unrealized,
                fees_paid=fees, equity=equity)
            body = dict(account_id=item.account_id, strategy_id=item.strategy_id,
                transaction_id=item.transaction_id, causation_id=item.causation_id,
                input_id=item.event_id, input_digest=stable_id("paper-account-input-v1", item),
                policy=state.config.policy, kind=item.kind, sequence=version,
                input_sequence=item.sequence, timestamp=item.timestamp,
                before_version=state.state_version, after_version=version,
                previous_event_id=state.last_event_id, **values)
            event = AccountEvent(event_id=stable_id("paper-account-event-v1", body), **body)
            updated = AccountSnapshot(config=state.config, **values, position=position,
                reservations=reservations, state_version=version, event_sequence=version,
                last_input_sequence=item.sequence, timestamp=item.timestamp,
                last_event_id=event.event_id,
                running_peak_equity=max(state.running_peak_equity, equity))
            updated.canonical_json()
            event.canonical_json()
            return updated, event
    except PaperFundingError:
        raise
    except (ValueError, TypeError, DecimalException) as exc:
        raise PaperInputError("invalid account transition") from exc


def _marked(position, source):
    with localcontext(exact_context()):
        mark = source.quote.bid if position.direction is PositionSide.LONG else source.quote.ask
        gross = (mark - position.entry_basis if position.direction is PositionSide.LONG
                 else position.entry_basis - mark) * position.quantity
        values = position.model_dump(mode="python")
        values.update(valuation=source, unrealized_pnl=gross)
        return AccountPosition(**values)
