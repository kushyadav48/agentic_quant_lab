"""Synthetic .bi5 fixtures and HTTP doubles: no live provider access."""

import hashlib
import lzma
import struct
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from urllib.error import HTTPError, URLError

import pytest
from pydantic import ValidationError

from quantlab.data import AssetClass, Instrument, MarketQuote, TradingCalendar
from quantlab.data.providers import (
    HistoricalQuoteRequest,
    ProviderConfigurationError,
    ProviderDataError,
    ProviderTransportError,
)
from quantlab.data.providers import dukascopy as duk

HOUR = datetime(2024, 1, 15, 12, tzinfo=timezone.utc)
URL = "https://datafeed.dukascopy.com/datafeed/EURUSD/2024/00/15/12h_ticks.bi5"


def bi5(*records: tuple[int, int, int, float, float]) -> bytes:
    """Construct a complete LZMA-Alone stream from native big-endian tick fields."""
    raw = b"".join(struct.pack(">IIIff", *record) for record in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


class FakeFetcher:
    def __init__(self, payload: bytes | None) -> None:
        self.payload = payload
        self.calls: list[tuple[str, float, int]] = []

    def __call__(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> bytes | None:
        self.calls.append((url, timeout, max_bytes))
        return self.payload


@pytest.fixture(autouse=True)
def forbid_live_http(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tests must never perform live HTTP requests")

    monkeypatch.setattr(duk, "urlopen", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)


@pytest.fixture
def instrument():
    return Instrument(
        instrument_id="fx:EURUSD",
        symbol="EUR/USD",
        asset_class=AssetClass.FOREX,
        base_currency="EUR",
        quote_currency="USD",
        tick_size=Decimal("0.00001"),
        quantity_increment=Decimal("1000"),
        pip_size=Decimal("0.0001"),
        lot_size=Decimal("100000"),
        calendar=TradingCalendar(calendar_id="fx-session-v1", timezone="UTC"),
    )


def request_for(instrument, **overrides):
    fields = {
        "instrument": instrument,
        "start_time": HOUR,
        "end_time": HOUR + timedelta(hours=1),
    }
    return HistoricalQuoteRequest(**{**fields, **overrides})


def test_eurusd_symbol_conversion_uses_metadata(instrument):
    assert duk.provider_symbol(instrument) == "EURUSD"
    fields = {**instrument.model_dump(), "symbol": "unrelated-display-label"}
    assert duk.provider_symbol(Instrument(**fields)) == "EURUSD"


def test_usdjpy_symbol_conversion_and_decimal_scaling(instrument):
    jpy = Instrument(
        **{
            **instrument.model_dump(),
            "instrument_id": "fx:USDJPY",
            "symbol": "USD/JPY",
            "base_currency": "USD",
            "quote_currency": "JPY",
            "tick_size": Decimal("0.001"),
            "pip_size": Decimal("0.01"),
        }
    )
    assert duk.provider_symbol(jpy) == "USDJPY"
    fetcher = FakeFetcher(bi5((250, 145126, 145123, 2.0, 3.0)))
    quotes = list(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(jpy)))
    assert quotes[0].bid == Decimal("145.123")
    assert quotes[0].ask == Decimal("145.126")
    assert "/USDJPY/" in fetcher.calls[0][0]


def test_native_record_fields_and_byte_order():
    ticks = duk._decode_ticks(
        bi5((1234, 110002, 110000, 1.25, 2.5)), max_bytes=1024
    )
    assert len(ticks) == 1
    tick = ticks[0]
    assert (tick.milliseconds, tick.ask, tick.bid) == (1234, 110002, 110000)
    assert tick.ask_volume == 1.25
    assert tick.bid_volume == 2.5


def test_utc_event_time_prices_and_provenance(instrument):
    payload = bi5((1234, 110002, 110000, 1.0, 2.0))
    fetcher = FakeFetcher(payload)
    quotes = list(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(instrument)))
    quote = quotes[0]
    assert isinstance(quote, MarketQuote)
    assert quote.timestamp == HOUR + timedelta(milliseconds=1234)
    assert quote.timestamp.tzinfo is timezone.utc
    assert quote.available_at == quote.timestamp
    assert quote.bid == Decimal("1.10000")
    assert quote.ask == Decimal("1.10002")
    assert quote.instrument_id == "fx:EURUSD"
    assert quote.source_id == "dukascopy"
    assert "EURUSD" in quote.dataset_id
    assert HOUR.isoformat() in quote.dataset_id
    assert hashlib.sha256(payload).hexdigest() in quote.dataset_id
    assert fetcher.calls[0][0] == URL


def test_scaling_is_independent_of_decimal_context(instrument):
    fetcher = FakeFetcher(bi5((0, 110002, 110000, 1.0, 1.0)))
    with localcontext() as context:
        context.prec = 2
        quote = next(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(instrument)))
    assert quote.bid == Decimal("1.10000")
    assert quote.ask == Decimal("1.10002")


def test_multiple_records_preserve_order_and_duplicates(instrument):
    fetcher = FakeFetcher(
        bi5((2000, 110003, 110001, 1.0, 1.0),
            (1000, 110002, 110000, 1.0, 1.0),
            (1000, 110002, 110000, 1.0, 1.0))
    )
    quotes = list(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(instrument)))
    assert [quote.timestamp for quote in quotes] == [
        HOUR + timedelta(seconds=2),
        HOUR + timedelta(seconds=1),
        HOUR + timedelta(seconds=1),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"not-lzma",
        bi5((0, 110002, 110000, 1.0, 1.0))[:-1],
        bi5((0, 110002, 110000, 1.0, 1.0)) + b"junk",
        bi5((0, 110002, 110000, 1.0, 1.0)) * 2,
        lzma.compress(b"\x00" * 20, format=lzma.FORMAT_XZ),
        lzma.compress(b"\x00" * 19, format=lzma.FORMAT_ALONE),
    ],
    ids=["not-lzma", "truncated-stream", "trailing-bytes",
         "concatenated-streams", "wrong-container", "truncated-record"],
)
def test_corrupt_payload_is_rejected(instrument, payload):
    provider = duk.DukascopyProvider(fetcher=FakeFetcher(payload))
    with pytest.raises(ProviderDataError, match="Malformed Dukascopy file"):
        list(provider.fetch(request_for(instrument)))


@pytest.mark.parametrize("offset", [3_600_000, 43_200_000, 2**32 - 1])
def test_hourly_offsets_reject_daily_or_out_of_hour_records(instrument, offset):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(bi5((offset, 110002, 110000, 1.0, 1.0)))
    )
    with pytest.raises(ProviderDataError, match="outside its UTC hour"):
        list(provider.fetch(request_for(instrument)))


@pytest.mark.parametrize(
    ("ask", "bid"), [(110000, 110002), (0, 110000), (110002, 0)]
)
def test_invalid_native_prices_cannot_produce_quotes(instrument, ask, bid):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(bi5((0, ask, bid, 1.0, 1.0)))
    )
    with pytest.raises(ProviderDataError):
        list(provider.fetch(request_for(instrument)))


def test_locked_native_quote_is_accepted(instrument):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(bi5((0, 110000, 110000, 0.0, 0.0)))
    )
    quote = next(provider.fetch(request_for(instrument)))
    assert quote.bid == quote.ask


@pytest.mark.parametrize("volume", [-1.0, float("nan"), float("inf")])
@pytest.mark.parametrize("side", ["ask", "bid"])
def test_malformed_provider_volume_is_not_silently_dropped(instrument, volume, side):
    volumes = (volume, 1.0) if side == "ask" else (1.0, volume)
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(bi5((0, 110002, 110000, *volumes)))
    )
    with pytest.raises(ProviderDataError, match="invalid provider volume"):
        list(provider.fetch(request_for(instrument)))


@pytest.mark.parametrize("payload", [None, b"", bi5()])
def test_absence_is_distinct_from_corruption(instrument, payload):
    provider = duk.DukascopyProvider(fetcher=FakeFetcher(payload))
    assert list(provider.fetch(request_for(instrument))) == []


def test_inclusive_start_exclusive_end_with_microseconds(instrument):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(
            bi5((999, 110002, 110000, 1.0, 1.0),
                (1000, 110002, 110000, 1.0, 1.0),
                (1500, 110002, 110000, 1.0, 1.0),
                (2000, 110002, 110000, 1.0, 1.0))
        )
    )
    request = request_for(
        instrument,
        start_time=HOUR + timedelta(seconds=1),
        end_time=HOUR + timedelta(seconds=2),
    )
    quotes = list(provider.fetch(request))
    assert [quote.timestamp for quote in quotes] == [
        HOUR + timedelta(seconds=1), HOUR + timedelta(milliseconds=1500)
    ]
    request = request_for(
        instrument,
        start_time=HOUR + timedelta(seconds=1, microseconds=1),
        end_time=HOUR + timedelta(seconds=2),
    )
    assert len(list(provider.fetch(request))) == 1


def test_hour_boundaries_fetch_only_intersecting_files(instrument):
    fetcher = FakeFetcher(bi5((0, 110002, 110000, 1.0, 1.0)))
    request = request_for(
        instrument,
        start_time=HOUR + timedelta(minutes=59),
        end_time=HOUR + timedelta(hours=2),
    )
    quotes = list(duk.DukascopyProvider(fetcher=fetcher).fetch(request))
    assert len(fetcher.calls) == 2
    assert fetcher.calls[0][0] == URL
    assert fetcher.calls[1][0] == URL.replace("12h_ticks", "13h_ticks")
    assert [quote.timestamp for quote in quotes] == [HOUR + timedelta(hours=1)]


def test_missing_hours_do_not_get_filled(instrument):
    def fetcher(url, **kwargs):
        return None if "12h_ticks" in url else bi5((0, 110002, 110000, 1.0, 1.0))

    request = request_for(instrument, end_time=HOUR + timedelta(hours=2))
    quotes = list(duk.DukascopyProvider(fetcher=fetcher).fetch(request))
    assert [quote.timestamp for quote in quotes] == [HOUR + timedelta(hours=1)]


def test_corrupt_out_of_range_record_invalidates_entire_hour(instrument):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(
            bi5((1000, 110002, 110000, 1.0, 1.0),
                (2000, 110000, 110002, 1.0, 1.0))
        )
    )
    request = request_for(instrument, end_time=HOUR + timedelta(milliseconds=1500))
    with pytest.raises(ProviderDataError):
        next(provider.fetch(request))


def test_offset_inputs_use_utc_hour_and_zero_based_month(instrument):
    offset = timezone(timedelta(hours=5, minutes=30))
    utc_start = datetime(2024, 12, 31, 23, tzinfo=timezone.utc)
    fetcher = FakeFetcher(bi5((0, 110002, 110000, 1.0, 1.0)))
    request = request_for(
        instrument, start_time=utc_start.astimezone(offset),
        end_time=(utc_start + timedelta(hours=1)).astimezone(offset),
    )
    quote = next(duk.DukascopyProvider(fetcher=fetcher).fetch(request))
    assert quote.timestamp == utc_start
    assert "/EURUSD/2024/11/31/23h_ticks.bi5" in fetcher.calls[0][0]


def test_range_crossing_year_fetches_next_year(instrument):
    start = datetime(2024, 12, 31, 23, 59, tzinfo=timezone.utc)
    fetcher = FakeFetcher(None)
    request = request_for(
        instrument, start_time=start, end_time=start + timedelta(minutes=2)
    )
    assert list(duk.DukascopyProvider(fetcher=fetcher).fetch(request)) == []
    assert "/2024/11/31/23h_ticks.bi5" in fetcher.calls[0][0]
    assert "/2025/00/01/00h_ticks.bi5" in fetcher.calls[1][0]


@pytest.mark.parametrize("field", ["start_time", "end_time"])
@pytest.mark.parametrize("value", [HOUR.replace(tzinfo=None), HOUR.isoformat(), date(2024, 1, 15)])
def test_request_rejects_naive_or_coerced_times(instrument, field, value):
    with pytest.raises(ValidationError):
        request_for(instrument, **{field: value})


@pytest.mark.parametrize("end", [HOUR, HOUR - timedelta(seconds=1)])
def test_request_rejects_empty_or_reversed_ranges(instrument, end):
    with pytest.raises(ValidationError, match="end_time must be after"):
        request_for(instrument, end_time=end)


@pytest.mark.parametrize("asset_class", [AssetClass.EQUITY, AssetClass.CRYPTO])
def test_non_forex_is_rejected_before_transport(instrument, asset_class):
    fields = instrument.model_dump()
    fields.update(asset_class=asset_class, pip_size=None, lot_size=None)
    non_forex = Instrument(**fields)
    fetcher = FakeFetcher(None)
    with pytest.raises(ProviderConfigurationError, match="Forex only"):
        list(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(non_forex)))
    assert fetcher.calls == []


def test_unregistered_forex_pair_is_not_guessed(instrument):
    gbp = Instrument(**{**instrument.model_dump(), "base_currency": "GBP"})
    fetcher = FakeFetcher(None)
    with pytest.raises(ProviderConfigurationError, match="Unsupported"):
        list(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(gbp)))
    assert fetcher.calls == []


def test_mismatched_price_metadata_is_rejected_before_transport(instrument):
    coarser = Instrument(**{**instrument.model_dump(), "tick_size": Decimal("0.0001")})
    fetcher = FakeFetcher(None)
    with pytest.raises(ProviderConfigurationError, match="requires tick_size"):
        list(duk.DukascopyProvider(fetcher=fetcher).fetch(request_for(coarser)))
    assert fetcher.calls == []


def test_configurable_timeout_is_forwarded(instrument):
    fetcher = FakeFetcher(None)
    provider = duk.DukascopyProvider(
        fetcher=fetcher,
        config=duk.DukascopyConfig(timeout_seconds=2.5, max_compressed_bytes=123),
    )
    assert list(provider.fetch(request_for(instrument))) == []
    assert fetcher.calls == [(URL, 2.5, 123)]


@pytest.mark.parametrize(
    ("field", "value"),
    [("timeout_seconds", 0.0), ("timeout_seconds", float("nan")),
     ("timeout_seconds", float("inf")), ("max_compressed_bytes", 0),
     ("max_decompressed_bytes", -1), ("max_compressed_bytes", True)],
)
def test_invalid_transport_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        duk.DukascopyConfig(**{field: value})


def test_injected_network_error_is_meaningful_and_not_retried(instrument):
    calls = []

    def failed_fetcher(url, **kwargs):
        calls.append(url)
        raise URLError("connection lost")

    provider = duk.DukascopyProvider(fetcher=failed_fetcher)
    with pytest.raises(ProviderTransportError, match="connection lost"):
        list(provider.fetch(request_for(instrument)))
    assert calls == [URL]


def test_transport_errors_are_not_converted_into_empty_results(instrument):
    def failed_fetcher(url, **kwargs):
        raise ProviderTransportError("rate limited", status_code=429)

    with pytest.raises(ProviderTransportError) as error:
        list(duk.DukascopyProvider(fetcher=failed_fetcher).fetch(request_for(instrument)))
    assert error.value.status_code == 429


def test_compressed_payload_limit_is_enforced_for_injected_transports(instrument):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(b"123456"),
        config=duk.DukascopyConfig(max_compressed_bytes=5),
    )
    with pytest.raises(ProviderDataError, match="too large"):
        list(provider.fetch(request_for(instrument)))


def test_decompressed_payload_limit_is_enforced(instrument):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(bi5((0, 110002, 110000, 1.0, 1.0))),
        config=duk.DukascopyConfig(max_decompressed_bytes=19),
    )
    with pytest.raises(ProviderDataError, match="exceeds 19"):
        list(provider.fetch(request_for(instrument)))


def test_exact_decompressed_limit_is_valid(instrument):
    provider = duk.DukascopyProvider(
        fetcher=FakeFetcher(bi5((0, 110002, 110000, 1.0, 1.0))),
        config=duk.DukascopyConfig(max_decompressed_bytes=20),
    )
    assert len(list(provider.fetch(request_for(instrument)))) == 1


def test_invalid_fetcher_return_type_is_not_absence(instrument):
    provider = duk.DukascopyProvider(fetcher=lambda *args, **kwargs: "HTML")
    with pytest.raises(ProviderDataError, match="bytes or None"):
        list(provider.fetch(request_for(instrument)))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, limit):
        self.read_limit = limit
        return self.payload[:limit]


def test_default_transport_reads_with_limits_and_closes(monkeypatch):
    response = FakeResponse(b"123")
    calls = []

    def fake_urlopen(url, *, timeout):
        calls.append((url, timeout))
        return response

    monkeypatch.setattr(duk, "urlopen", fake_urlopen)
    assert duk.fetch_bytes(URL, timeout=2.0, max_bytes=3) == b"123"
    assert calls == [(URL, 2.0)]
    assert response.read_limit == 4
    assert response.closed


def test_default_transport_enforces_compressed_limit(monkeypatch):
    response = FakeResponse(b"1234")
    monkeypatch.setattr(duk, "urlopen", lambda *args, **kwargs: response)
    with pytest.raises(ProviderDataError, match="exceeds 3"):
        duk.fetch_bytes(URL, timeout=2.0, max_bytes=3)
    assert response.closed


def test_default_transport_404_returns_explicit_absence(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError(URL, 404, "not found", None, None)

    monkeypatch.setattr(duk, "urlopen", missing)
    assert duk.fetch_bytes(URL, timeout=2.0, max_bytes=100) is None


@pytest.mark.parametrize(("status", "retryable"), [(429, True), (503, True), (403, False)])
def test_default_transport_distinguishes_http_failures(monkeypatch, status, retryable):
    calls = []

    def failed(*args, **kwargs):
        calls.append(args[0])
        raise HTTPError(URL, status, "failed", None, None)

    monkeypatch.setattr(duk, "urlopen", failed)
    with pytest.raises(ProviderTransportError) as error:
        duk.fetch_bytes(URL, timeout=2.0, max_bytes=100)
    assert error.value.status_code == status
    assert error.value.retryable is retryable
    assert calls == [URL]


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError("timed out")])
def test_default_transport_surfaces_network_failure(monkeypatch, failure):
    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(duk, "urlopen", failed)
    with pytest.raises(ProviderTransportError, match="download failed"):
        duk.fetch_bytes(URL, timeout=2.0, max_bytes=100)


def test_fetch_is_lazy_and_imports_have_no_http_side_effects(instrument):
    fetcher = FakeFetcher(None)
    provider = duk.DukascopyProvider(fetcher=fetcher)
    iterator = provider.fetch(request_for(instrument))
    assert fetcher.calls == []
    assert list(iterator) == []
    code = (
        "import urllib.request; "
        "urllib.request.urlopen=lambda *a, **k: (_ for _ in ()).throw("
        "AssertionError('network during import')); "
        "import quantlab.data.providers; "
        "import quantlab.data.providers.dukascopy"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
