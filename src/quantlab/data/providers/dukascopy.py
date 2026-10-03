"""Dukascopy's public hourly Forex tick archive, isolated from domain contracts.

Official format reference:
https://www.dukascopy.com/wiki/en/development/data-export/
The guide documents the tick fields and warns that hourly and daily time bases
differ. This adapter selects only the hourly public datafeed layout; it does not
read the daily Requester Pays S3 layout or detect formats heuristically.

Hourly path: SYMBOL/YYYY/MM/DD/HHh_ticks.bi5 (MM is zero-based; all dates UTC).
Compression: legacy LZMA-Alone stream, not an XZ container or headerless raw LZMA.
Decompressed records: 20 bytes, big-endian >IIIff:
uint32 milliseconds into the hour, uint32 ask, uint32 bid,
float32 ask volume, float32 bid volume (millions of base-currency units).
Volumes are checked for finite/nonnegative values, but MarketQuote has no
volume fields, so they are not mapped. No ticks are aggregated or persisted.
"""

import hashlib
import lzma
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from http.client import HTTPException
from typing import Annotated, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quantlab._decimal import deterministic_context
from ..enums import AssetClass
from ..models import Instrument, MarketQuote
from .base import (
    HistoricalQuoteRequest,
    ProviderConfigurationError,
    ProviderDataError,
    ProviderTransportError,
)

_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
_RECORD = struct.Struct(">IIIff")
_HOUR = timedelta(hours=1)
_HOUR_MILLISECONDS = 3_600_000
_LZMA_MEMORY_LIMIT = 64 * 1024 * 1024


class DukascopyConfig(BaseModel):
    """Finite timeout and bounded per-hour payload sizes; no automatic retries."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 15.0
    max_compressed_bytes: Annotated[int, Field(gt=0)] = 8 * 1024 * 1024
    max_decompressed_bytes: Annotated[int, Field(gt=0)] = 32 * 1024 * 1024


class ByteFetcher(Protocol):
    """Injectable HTTP boundary; None explicitly denotes an absent resource.

    Empty successful bodies are the adapter's no-record policy. Other nonempty
    responses must decode completely. Errors must not masquerade as no data.
    """

    def __call__(
        self, url: str, *, timeout: float, max_bytes: int
    ) -> bytes | None:
        ...


def fetch_bytes(url: str, *, timeout: float, max_bytes: int) -> bytes | None:
    """Fetch one file using urllib; 404 is absent, all other failures surface."""
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            return None
        raise ProviderTransportError(
            f"Dukascopy HTTP {exc.code} for {url}",
            status_code=exc.code,
            retryable=exc.code == 429 or 500 <= exc.code < 600,
        ) from exc
    except (URLError, OSError, HTTPException) as exc:
        raise ProviderTransportError(f"Dukascopy download failed for {url}: {exc}") from exc
    if len(payload) > max_bytes:
        raise ProviderDataError(f"Dukascopy response exceeds {max_bytes} bytes: {url}")
    return payload


def provider_symbol(instrument: Instrument) -> str:
    """Explicit initial pair registry; display symbols do not drive conversion."""
    if instrument.asset_class is not AssetClass.FOREX:
        raise ProviderConfigurationError("Dukascopy V1 ingestion supports Forex only")
    pair = (instrument.base_currency, instrument.quote_currency)
    if pair == ("EUR", "USD"):
        symbol, point_size = "EURUSD", Decimal("0.00001")
    elif pair == ("USD", "JPY"):
        symbol, point_size = "USDJPY", Decimal("0.001")
    else:
        raise ProviderConfigurationError(f"Unsupported Dukascopy V1 Forex pair: {pair}")
    if instrument.tick_size != point_size:
        raise ProviderConfigurationError(
            f"{symbol} requires tick_size {point_size} for native integer scaling"
        )
    return symbol


@dataclass(frozen=True)
class _Tick:
    milliseconds: int
    ask: int
    bid: int
    ask_volume: float
    bid_volume: float


def _decode_ticks(payload: bytes, *, max_bytes: int) -> tuple[_Tick, ...]:
    """Decode a whole hourly file strictly, preserving its original record order."""
    if not payload:
        return ()
    try:
        decoder = lzma.LZMADecompressor(
            format=lzma.FORMAT_ALONE, memlimit=_LZMA_MEMORY_LIMIT
        )
        raw = decoder.decompress(payload, max_length=max_bytes + 1)
    except lzma.LZMAError as exc:
        raise ProviderDataError("Invalid Dukascopy LZMA-Alone stream") from exc
    if len(raw) > max_bytes:
        raise ProviderDataError(f"Decoded Dukascopy payload exceeds {max_bytes} bytes")
    if not decoder.eof:
        raise ProviderDataError("Truncated Dukascopy LZMA stream")
    if decoder.unused_data:
        raise ProviderDataError("Trailing bytes or multiple Dukascopy LZMA streams")
    if len(raw) % _RECORD.size:
        raise ProviderDataError("Truncated Dukascopy tick record (expected 20-byte records)")

    ticks = []
    for index, fields in enumerate(_RECORD.iter_unpack(raw)):
        tick = _Tick(*fields)
        if tick.milliseconds >= _HOUR_MILLISECONDS:
            raise ProviderDataError(f"Tick {index}: timestamp offset is outside its UTC hour")
        if any(
            not math.isfinite(volume) or volume < 0
            for volume in (tick.ask_volume, tick.bid_volume)
        ):
            raise ProviderDataError(f"Tick {index}: invalid provider volume")
        ticks.append(tick)
    return tuple(ticks)


def _to_quote(
    tick: _Tick, *, instrument: Instrument, hour: datetime, dataset_id: str
) -> MarketQuote:
    """Translate native prices using validated instrument metadata, not heuristics."""
    timestamp = hour + timedelta(milliseconds=tick.milliseconds)
    # A fixed local context makes integer scaling independent of caller precision.
    with localcontext(deterministic_context(prec=32)):
        bid = Decimal(tick.bid) * instrument.tick_size
        ask = Decimal(tick.ask) * instrument.tick_size
    return MarketQuote(
        instrument_id=instrument.instrument_id,
        source_id="dukascopy",
        dataset_id=dataset_id,
        timestamp=timestamp,
        available_at=timestamp,
        bid=bid,
        ask=ask,
    )


class DukascopyProvider:
    """Lazy hourly HTTP ingestion; fetch() yields quotes over [start_time, end_time).

    available_at equals the historical source-event timestamp, not download time.
    This assumes event availability without transport/publication latency; live
    adapters and later execution simulations must specify their own latency.

    No file/date availability guarantee is made. 404, empty HTTP bodies, and
    valid zero-record streams yield no observations. Other failures abort rather
    than being replaced by gaps. An earlier hour may already have been yielded
    when a later download fails; iterator exhaustion means the request completed.
    """

    def __init__(
        self,
        *,
        config: DukascopyConfig | None = None,
        fetcher: ByteFetcher | None = None,
    ) -> None:
        self._config = config if config is not None else DukascopyConfig()
        self._fetcher = fetcher if fetcher is not None else fetch_bytes

    def fetch(self, request: HistoricalQuoteRequest) -> Iterator[MarketQuote]:
        """Download only intersecting UTC hours; never fill, sort, or deduplicate."""
        symbol = provider_symbol(request.instrument)
        hour = request.start_time.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        while hour < request.end_time:
            path = (
                f"{symbol}/{hour.year:04d}/{hour.month - 1:02d}/"
                f"{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"
            )
            url = f"{_BASE_URL}/{path}"
            try:
                payload = self._fetcher(
                    url,
                    timeout=self._config.timeout_seconds,
                    max_bytes=self._config.max_compressed_bytes,
                )
            except (URLError, OSError, HTTPException) as exc:
                raise ProviderTransportError(
                    f"Dukascopy injected transport failed for {url}: {exc}"
                ) from exc
            if payload is not None:
                if not isinstance(payload, bytes):
                    raise ProviderDataError(f"Transport must return bytes or None: {url}")
                if len(payload) > self._config.max_compressed_bytes:
                    raise ProviderDataError(f"Compressed Dukascopy payload is too large: {url}")
                digest = hashlib.sha256(payload).hexdigest()
                dataset_id = f"dukascopy:ticks:{symbol}:{hour.isoformat()}:sha256:{digest}"
                try:
                    ticks = _decode_ticks(
                        payload, max_bytes=self._config.max_decompressed_bytes
                    )
                    # Validate the entire file, including records outside the requested
                    # subrange, before yielding any quotes from this hour.
                    quotes = tuple(
                        _to_quote(
                            tick, instrument=request.instrument, hour=hour,
                            dataset_id=dataset_id,
                        )
                        for tick in ticks
                    )
                except (ProviderDataError, ValidationError) as exc:
                    raise ProviderDataError(f"Malformed Dukascopy file {url}: {exc}") from exc
                for quote in quotes:
                    if request.start_time <= quote.timestamp < request.end_time:
                        yield quote
            if request.end_time - hour <= _HOUR:
                break
            hour += _HOUR
