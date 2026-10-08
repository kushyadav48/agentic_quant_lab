"""Offline fixtures: no strategy approval or account ledger."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantlab.backtesting import ExecutionCostConfig
from quantlab.data import AssetClass, Instrument, MarketQuote, TradingCalendar
from quantlab.paper import (
    CancellationRequest, KernelConfig, MarketDelivery, OrderSide,
    OrderSubmission, PaperOrderKernel,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SECOND = timedelta(seconds=1)
INSTRUMENT = Instrument(instrument_id="paper:TEST", symbol="TEST", asset_class=AssetClass.EQUITY,
    quote_currency="USD", tick_size=D("0.01"), quantity_increment=D("1"),
    calendar=TradingCalendar(calendar_id="fixture", timezone="UTC"))


def config(**kwargs):
    return KernelConfig(session_id="fixture", instrument=INSTRUMENT,
        flat_equity=kwargs.pop("flat_equity", D("1000")),
        running_peak_equity=kwargs.pop("running_peak_equity", D("1000")), **kwargs)


def market(sequence=1, *, event_id=None, bid="100", ask="102", observed=None,
           delivered=None, available=None, processed=None):
    observed = START if observed is None else observed
    delivered = observed if delivered is None else delivered
    available = observed if available is None else available
    return MarketDelivery(event_id=event_id or f"quote-{sequence}", sequence=sequence,
        timestamp=delivered if processed is None else processed, delivered_at=delivered,
        quote=MarketQuote(instrument_id=INSTRUMENT.instrument_id, source_id="synthetic",
            dataset_id="fixture", timestamp=observed, available_at=available,
            bid=D(bid), ask=D(ask)))


def submission(sequence=2, *, side=OrderSide.BUY, quantity="2", **kwargs):
    return OrderSubmission(command_id=kwargs.pop("command_id", "submit-1"),
        sequence=sequence, timestamp=kwargs.pop("timestamp", START),
        causation_id=kwargs.pop("causation_id", "quote-1"),
        instrument_id=kwargs.pop("instrument_id", INSTRUMENT.instrument_id),
        side=side, quantity=D(quantity), **kwargs)


def accepted(*, cfg=None, side=OrderSide.BUY):
    kernel = PaperOrderKernel(config() if cfg is None else cfg)
    kernel.process(market())
    kernel.process(submission(side=side))
    return kernel


def cancel(kernel, sequence=3, **kwargs):
    return CancellationRequest(command_id=kwargs.pop("command_id", "cancel-1"),
        sequence=sequence, timestamp=kwargs.pop("timestamp", START),
        causation_id=kwargs.pop("causation_id", "submit-1"),
        order_id=kwargs.pop("order_id", kernel.snapshot.order_id), **kwargs)


def priced_config(**kwargs):
    return config(costs=ExecutionCostConfig(slippage=D("0.5"),
        commission_per_unit=D("0.1"), fixed_fee_per_fill=D("1")), **kwargs)
