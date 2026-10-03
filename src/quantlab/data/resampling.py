"""Fixed UTC, left-labelled, half-open OHLC aggregation; no calendar inference."""
import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from enum import StrEnum
from typing import Self
from pydantic import model_validator
from quantlab._decimal import deterministic_context
from .enums import PriceType, Timeframe
from .models import Instrument, MarketBar, MarketQuote, UtcTimestamp, _DomainModel
from .validation import Observation, validate_dataset

DURATIONS = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
}
ANCHOR = datetime(1970, 1, 5, tzinfo=timezone.utc)  # UTC origin for intraday alignment

class MissingDataPolicy(StrEnum):
    OMIT = "omit"
    REJECT = "reject"

class ResampleRequest(_DomainModel):
    instrument: Instrument
    timeframe: Timeframe
    price_type: PriceType
    start_time: UtcTimestamp
    end_time: UtcTimestamp
    missing_policy: MissingDataPolicy  # deliberately no default

    @model_validator(mode="after")
    def check_window(self) -> Self:
        if self.timeframe not in DURATIONS:
            raise ValueError(
                f"{self.timeframe.value} resampling is unsupported: daily and weekly bars "
                "require explicit market/session/calendar semantics; "
                "supported targets are M1, M5, M15, M30, H1, H4"
            )
        duration = DURATIONS[self.timeframe]
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if any((t - ANCHOR) % duration for t in (self.start_time, self.end_time)):
            raise ValueError("request must contain whole aligned UTC target intervals")
        return self


def _identity(records: tuple[Observation, ...], request: ResampleRequest) -> str:
    body = {"version": "utc-ohlc-v2", "request": request.model_dump(mode="json"),
            "inputs": [r.model_dump(mode="json") for r in records]}
    return "resampled:sha256:" + hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resample(observations: Iterable[Observation], request: ResampleRequest) -> tuple[MarketBar, ...]:
    """Aggregate quotes OR lower-timeframe bars; strict order and no cleaning.

    Quotes select bid/ask/mid; trade cannot be reconstructed. Quote volume and
    volume_type remain None because quotes carry no canonical volume. Empty quote bins are missing, not proof of closure or
    coverage. Bar bins require contiguous complete input coverage; incomplete
    bins are omitted or rejected. No carry-forward, synthetic prices, upsampling,
    interval splitting, or mixing sources. Availability is max(end, inputs).
    """
    request = ResampleRequest.model_validate(request)
    records = tuple(observations)
    report = validate_dataset(records, instrument=request.instrument)
    if not report.valid:
        raise ValueError(f"invalid input dataset: {report.issues}")
    if len({r.source_id for r in records}) > 1:
        raise ValueError("resampling requires one source")
    quotes = not records or isinstance(records[0], MarketQuote)
    if quotes and request.price_type is PriceType.TRADE:
        raise ValueError("trade prices cannot be derived from quotes")
    duration = DURATIONS[request.timeframe]
    buckets = {}
    for record in records:
        start = record.timestamp if quotes else record.start_time
        if not request.start_time <= start < request.end_time:
            raise ValueError("input outside requested interval")
        bucket = request.start_time + ((start - request.start_time) // duration) * duration
        if not quotes:
            if record.timeframe not in DURATIONS:
                raise ValueError(
                    "daily and weekly input bars require explicit market/session/calendar semantics"
                )
            if record.price_type is not request.price_type:
                raise ValueError("bar price basis mismatch")
            if DURATIONS[record.timeframe] > duration or duration % DURATIONS[record.timeframe]:
                raise ValueError("target must be an integral coarsening of input timeframe")
            if record.end_time - start != DURATIONS[record.timeframe] or (start - ANCHOR) % DURATIONS[record.timeframe]:
                raise ValueError("input bars must be aligned fixed UTC timeframe intervals")
            if record.end_time > bucket + duration:
                raise ValueError("input bar crosses target boundary")
        buckets.setdefault(bucket, []).append(record)
    identity = _identity(records, request)
    result = []
    start = request.start_time
    while start < request.end_time:
        end = start + duration
        group = buckets.get(start, [])
        complete = bool(group)
        if group and not quotes:
            complete = (group[0].start_time == start and group[-1].end_time == end
                        and all(a.end_time == b.start_time for a, b in zip(group, group[1:])))
        if not complete:
            if request.missing_policy is MissingDataPolicy.REJECT:
                raise ValueError(f"missing or incomplete target interval: {start.isoformat()}")
            start = end
            continue
        decimals = [value for r in group for value in
                    ((r.bid, r.ask) if quotes else (r.open, r.high, r.low, r.close, r.volume))
                    if value is not None]
        precision = max(v.adjusted() for v in decimals) - min(v.as_tuple().exponent for v in decimals)
        with localcontext(deterministic_context(prec=max(32, precision + len(str(len(group))) + 4))):
            if quotes:
                prices = [r.bid if request.price_type is PriceType.BID else
                          r.ask if request.price_type is PriceType.ASK else
                          (r.bid + r.ask) / Decimal(2) for r in group]
                o, h, l, c = prices[0], max(prices), min(prices), prices[-1]
                volume, unit = None, None
            else:
                o, h, l, c = group[0].open, max(r.high for r in group), min(r.low for r in group), group[-1].close
                units = {r.volume_type for r in group if r.volume is not None}
                if len(units) > 1:
                    raise ValueError("cannot combine different volume units")
                volume = sum((r.volume for r in group), Decimal(0)) if all(r.volume is not None for r in group) else None
                unit = next(iter(units)) if volume is not None else None
            result.append(MarketBar(
                instrument_id=request.instrument.instrument_id, source_id=group[0].source_id,
                dataset_id=identity, timeframe=request.timeframe, price_type=request.price_type,
                start_time=start, end_time=end, available_at=max(end, *(r.available_at for r in group)),
                open=o, high=h, low=l, close=c, volume=volume, volume_type=unit))
        start = end
    return tuple(result)
