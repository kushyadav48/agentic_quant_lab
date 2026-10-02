"""Behavioral checks for typed, provider-neutral market-data contracts."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantlab.data import (
    AssetClass,
    Instrument,
    MarketBar,
    MarketQuote,
    PriceType,
    Timeframe,
    TradingCalendar,
    VolumeType,
)

UTC = timezone.utc
START = datetime(2026, 1, 5, 10, tzinfo=UTC)
END = START + timedelta(minutes=1)


@pytest.fixture
def instrument_fields():
    return {
        "instrument_id": "forex:EURUSD",
        "symbol": "EUR/USD",
        "asset_class": AssetClass.FOREX,
        "base_currency": "EUR",
        "quote_currency": "USD",
        "tick_size": Decimal("0.00001"),
        "quantity_increment": Decimal("1000"),
        "pip_size": Decimal("0.0001"),
        "lot_size": Decimal("100000"),
        "calendar": TradingCalendar(
            calendar_id="fx-session-v1", timezone="America/New_York"
        ),
    }


@pytest.fixture
def bar_fields():
    return {
        "instrument_id": "forex:EURUSD",
        "source_id": "fixture-source",
        "dataset_id": "fixture-v1",
        "timeframe": Timeframe.M1,
        "price_type": PriceType.BID,
        "start_time": START,
        "end_time": END,
        "available_at": END,
        "open": Decimal("1.10000"),
        "high": Decimal("1.10100"),
        "low": Decimal("1.09900"),
        "close": Decimal("1.10050"),
    }


@pytest.fixture
def quote_fields():
    return {
        "instrument_id": "forex:EURUSD",
        "source_id": "fixture-source",
        "dataset_id": "fixture-v1",
        "timestamp": START,
        "available_at": START,
        "bid": Decimal("1.10000"),
        "ask": Decimal("1.10002"),
    }


def test_forex_metadata_and_decimal_precision(instrument_fields):
    instrument = Instrument(**instrument_fields)
    assert instrument.asset_class is AssetClass.FOREX
    assert instrument.tick_size == Decimal("0.00001")
    assert instrument.lot_size == Decimal("100000")
    assert instrument.contract_multiplier == Decimal("1")
    assert instrument.calendar.timezone == "America/New_York"


def test_jpy_forex_conventions_are_not_hardcoded(instrument_fields):
    instrument = Instrument(
        **{
            **instrument_fields,
            "instrument_id": "forex:USDJPY",
            "symbol": "USD/JPY",
            "base_currency": "USD",
            "quote_currency": "JPY",
            "tick_size": Decimal("0.001"),
            "pip_size": Decimal("0.01"),
        }
    )
    assert instrument.pip_size == Decimal("0.01")


@pytest.mark.parametrize(
    ("asset_class", "base_currency", "quote_currency"),
    [(AssetClass.EQUITY, None, "USD"), (AssetClass.CRYPTO, "BTC", "USDT")],
)
def test_non_forex_metadata_has_no_pip_or_lot_requirement(
    instrument_fields, asset_class, base_currency, quote_currency
):
    fields = {
        **instrument_fields,
        "asset_class": asset_class,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
    }
    fields.pop("pip_size")
    fields.pop("lot_size")
    instrument = Instrument(**fields)
    assert instrument.pip_size is None
    assert instrument.lot_size is None


@pytest.mark.parametrize("field", ["base_currency", "pip_size", "lot_size"])
def test_forex_requires_explicit_market_conventions(instrument_fields, field):
    instrument_fields.pop(field)
    with pytest.raises(ValidationError, match="Forex requires"):
        Instrument(**instrument_fields)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instrument_id", ""),
        ("symbol", " EUR/USD"),
        ("base_currency", "eur"),
        ("base_currency", "EUR1"),
        ("base_currency", "USD"),
        ("quote_currency", "USDT"),
        ("tick_size", Decimal("0")),
        ("quantity_increment", Decimal("-1")),
        ("contract_multiplier", Decimal("Infinity")),
        ("pip_size", Decimal("0.000001")),
        ("lot_size", Decimal("0")),
    ],
)
def test_invalid_instrument_metadata_is_rejected(instrument_fields, field, value):
    with pytest.raises(ValidationError):
        Instrument(**{**instrument_fields, field: value})


def test_forex_only_fields_are_rejected_for_other_markets(instrument_fields):
    with pytest.raises(ValidationError, match="Forex-specific"):
        Instrument(**{**instrument_fields, "asset_class": AssetClass.EQUITY})


@pytest.mark.parametrize("field", ["calendar_id", "timezone"])
@pytest.mark.parametrize("value", ["", " ", " name", "name "])
def test_calendar_reference_rejects_empty_or_padded_labels(field, value):
    fields = {"calendar_id": "fx-session-v1", "timezone": "America/New_York"}
    with pytest.raises(ValidationError):
        TradingCalendar(**{**fields, field: value})


def test_bar_preserves_price_basis_and_missing_volume(bar_fields):
    bar = MarketBar(**bar_fields)
    assert bar.price_type is PriceType.BID
    assert bar.volume is None
    assert bar.volume_type is None


@pytest.mark.parametrize("price_type", list(PriceType))
def test_all_explicit_price_bases_are_supported(bar_fields, price_type):
    assert MarketBar(**{**bar_fields, "price_type": price_type}).price_type is price_type


def test_price_basis_is_not_assumed(bar_fields):
    bar_fields.pop("price_type")
    with pytest.raises(ValidationError):
        MarketBar(**bar_fields)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", Decimal("1.102")),
        ("close", Decimal("1.098")),
        ("high", Decimal("1.098")),
        ("low", Decimal("1.102")),
        ("open", Decimal("0")),
        ("high", Decimal("NaN")),
        ("low", Decimal("-Infinity")),
        ("close", Decimal("Infinity")),
    ],
)
def test_invalid_ohlc_is_rejected(bar_fields, field, value):
    with pytest.raises(ValidationError):
        MarketBar(**{**bar_fields, field: value})


def test_flat_bar_is_valid(bar_fields):
    fields = {**bar_fields, **dict.fromkeys(("open", "high", "low", "close"), Decimal("1"))}
    assert MarketBar(**fields).high == Decimal("1")


@pytest.mark.parametrize(
    ("volume", "volume_type"),
    [
        (Decimal("0"), VolumeType.TICK_COUNT),
        (Decimal("25"), VolumeType.TICK_COUNT),
        (Decimal("1.25"), VolumeType.BASE),
        (Decimal("10.50"), VolumeType.QUOTE),
    ],
)
def test_explicit_volume_units_and_zero_volume(bar_fields, volume, volume_type):
    bar = MarketBar(**bar_fields, volume=volume, volume_type=volume_type)
    assert bar.volume == volume
    assert bar.volume_type is volume_type


@pytest.mark.parametrize(
    ("volume", "volume_type"),
    [
        (Decimal("-1"), VolumeType.BASE),
        (Decimal("NaN"), VolumeType.BASE),
        (Decimal("Infinity"), VolumeType.BASE),
        (Decimal("1.5"), VolumeType.TICK_COUNT),
        (Decimal("1"), None),
        (None, VolumeType.BASE),
    ],
)
def test_ambiguous_or_invalid_volume_is_rejected(bar_fields, volume, volume_type):
    with pytest.raises(ValidationError):
        MarketBar(**bar_fields, volume=volume, volume_type=volume_type)


@pytest.mark.parametrize("field", ["start_time", "end_time", "available_at"])
def test_bar_requires_aware_timestamps(bar_fields, field):
    with pytest.raises(ValidationError, match="timezone-aware"):
        MarketBar(**{**bar_fields, field: bar_fields[field].replace(tzinfo=None)})


def test_offset_timestamps_normalize_to_utc(bar_fields):
    offset = timezone(timedelta(hours=5, minutes=30))
    fields = {
        **bar_fields,
        **{key: bar_fields[key].astimezone(offset)
           for key in ("start_time", "end_time", "available_at")},
    }
    bar = MarketBar(**fields)
    assert bar.start_time == START
    assert bar.end_time == END
    assert bar.available_at == END
    assert all(
        value.tzinfo is UTC for value in (bar.start_time, bar.end_time, bar.available_at)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("end_time", START),
        ("end_time", START - timedelta(minutes=1)),
        ("available_at", END - timedelta(microseconds=1)),
    ],
)
def test_invalid_bar_interval_or_availability_is_rejected(bar_fields, field, value):
    with pytest.raises(ValidationError):
        MarketBar(**{**bar_fields, field: value})


def test_publication_delay_and_non_24_hour_daily_bar_are_valid(bar_fields):
    end = START + timedelta(hours=23)
    bar = MarketBar(
        **{
            **bar_fields,
            "timeframe": Timeframe.D1,
            "end_time": end,
            "available_at": end + timedelta(minutes=5),
        }
    )
    assert bar.end_time - bar.start_time == timedelta(hours=23)
    assert bar.available_at > bar.end_time


@pytest.mark.parametrize("field", ["timestamp", "available_at"])
def test_quote_requires_aware_timestamps(quote_fields, field):
    with pytest.raises(ValidationError, match="timezone-aware"):
        MarketQuote(**{**quote_fields, field: START.replace(tzinfo=None)})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ask", Decimal("1.09")),
        ("bid", Decimal("0")),
        ("ask", Decimal("NaN")),
        ("bid", Decimal("Infinity")),
        ("available_at", START - timedelta(microseconds=1)),
    ],
)
def test_invalid_quotes_are_rejected(quote_fields, field, value):
    with pytest.raises(ValidationError):
        MarketQuote(**{**quote_fields, field: value})


def test_locked_quote_and_delayed_availability_are_valid(quote_fields):
    quote = MarketQuote(
        **{**quote_fields, "ask": quote_fields["bid"],
           "available_at": START + timedelta(seconds=1)}
    )
    assert quote.bid == quote.ask
    assert quote.available_at > quote.timestamp


@pytest.mark.parametrize("value", ["1.10000", 1.1, 1, True])
def test_python_numeric_coercion_is_rejected(bar_fields, value):
    with pytest.raises(ValidationError):
        MarketBar(**{**bar_fields, "open": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("price_type", "bid"), ("timeframe", "1m"), ("start_time", START.isoformat())],
)
def test_python_enum_and_datetime_coercion_is_rejected(bar_fields, field, value):
    with pytest.raises(ValidationError):
        MarketBar(**{**bar_fields, field: value})


@pytest.mark.parametrize(
    ("model", "fixture"),
    [(Instrument, "instrument_fields"), (MarketBar, "bar_fields"),
     (MarketQuote, "quote_fields")],
)
def test_unknown_fields_are_rejected(model, fixture, request):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model(**request.getfixturevalue(fixture), provider_specific_option=True)


@pytest.mark.parametrize("field", ["instrument_id", "source_id", "dataset_id"])
def test_observations_require_nonblank_provenance_identifiers(bar_fields, field):
    with pytest.raises(ValidationError):
        MarketBar(**{**bar_fields, field: " "})


@pytest.mark.parametrize(
    ("model", "fixture", "field", "value"),
    [(Instrument, "instrument_fields", "symbol", "USD/JPY"),
     (MarketBar, "bar_fields", "close", Decimal("1")),
     (MarketQuote, "quote_fields", "bid", Decimal("1"))],
)
def test_models_are_immutable(model, fixture, field, value, request):
    instance = model(**request.getfixturevalue(fixture))
    with pytest.raises(ValidationError, match="frozen"):
        setattr(instance, field, value)


def test_nested_calendar_is_immutable(instrument_fields):
    instrument = Instrument(**instrument_fields)
    with pytest.raises(ValidationError, match="frozen"):
        instrument.calendar.timezone = "UTC"


@pytest.mark.parametrize(
    ("model", "fixture"),
    [(Instrument, "instrument_fields"), (MarketBar, "bar_fields"),
     (MarketQuote, "quote_fields")],
)
def test_json_round_trip_preserves_domain_values(model, fixture, request):
    instance = model(**request.getfixturevalue(fixture))
    assert model.model_validate_json(instance.model_dump_json()) == instance
    assert model.model_json_schema()["additionalProperties"] is False
