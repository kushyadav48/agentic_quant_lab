"""Small synthetic research series shared by feature tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from quantlab.data import AssetClass, Instrument, TradingCalendar, MarketBar, Timeframe, PriceType
from quantlab.features import FeatureParameter, FeatureRequest, compute_features

D = Decimal
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
INSTRUMENT = Instrument(instrument_id="fx:EURUSD", symbol="EUR/USD", asset_class=AssetClass.FOREX,
    base_currency="EUR", quote_currency="USD", tick_size=D("0.00001"),
    quantity_increment=D("1000"), pip_size=D("0.0001"), lot_size=D("100000"),
    calendar=TradingCalendar(calendar_id="fixture", timezone="UTC"))


def bars(prices=(1, 2, 3, 4, 5)):
    return tuple(MarketBar(instrument_id=INSTRUMENT.instrument_id, source_id="synthetic",
        dataset_id="fixture", timeframe=Timeframe.M1, price_type=PriceType.BID,
        start_time=START+i*MINUTE, end_time=START+(i+1)*MINUTE,
        available_at=START+(i+1)*MINUTE, open=D(p), high=D(p), low=D(p), close=D(p))
        for i, p in enumerate(prices))


def request(name, n=None, **kwargs):
    return FeatureRequest(feature_id=name, parameters=() if n is None else (
        FeatureParameter(name="window" if name == "rolling_volatility" else "period", value=n),), **kwargs)


def compute(series, *requests):
    return compute_features(series, requests, instrument=INSTRUMENT)


def values(series, name, n=None):
    return tuple(o.value for o in compute(series, request(name, n)))
