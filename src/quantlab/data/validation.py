"""Read-only quality checks and explicit normalization of canonical observations."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, ValidationError, model_validator

from .models import (
    Identifier, Instrument, MarketBar, MarketQuote, UtcTimestamp, _DomainModel, _as_utc,
)

Observation = MarketQuote | MarketBar


class ValidationOptions(_DomainModel):
    """Irregular ticks have no assumed cadence; thresholds are opt-in."""

    max_gap: timedelta | None = None
    homogeneous_source: bool = True
    homogeneous_dataset: bool = False
    expected_source_id: Identifier | None = None
    expected_dataset_id: Identifier | None = None

    @model_validator(mode="after")
    def check_threshold(self) -> Self:
        if self.max_gap is not None and self.max_gap <= timedelta(0):
            raise ValueError("max_gap must be positive")
        return self


class QualityIssue(_DomainModel):
    code: Identifier
    index: int | None = Field(default=None, ge=0)
    detail: str


class DataQualityReport(_DomainModel):
    observation_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    duplicate_timestamp_count: int = Field(ge=0)
    out_of_order_count: int = Field(ge=0)
    non_monotonic_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    first_timestamp: UtcTimestamp | None
    last_timestamp: UtcTimestamp | None
    warnings: tuple[QualityIssue, ...] = ()
    errors: tuple[QualityIssue, ...] = ()
    gaps_checked: bool

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        return self.errors + self.warnings

    @property
    def record_count(self) -> int:
        return self.observation_count


QualityReport = DataQualityReport


def _event_time(record: Observation) -> datetime:
    return record.timestamp if isinstance(record, MarketQuote) else record.start_time


def validate_dataset(
    observations: Iterable[Observation],
    *,
    instrument: Instrument,
    options: ValidationOptions | None = None,
    expected_starts: Iterable[datetime] | None = None,
) -> DataQualityReport:
    """Report without sorting, repairing or removing observations.

    Exact duplicates include provenance and availability; repeated timestamps
    are counted separately. Ordering counts adjacent decreases; non-monotonic
    counts adjacent times <= their predecessor. Quote gaps use max_gap only,
    never a tick cadence. Optional expected_starts is a caller-owned schedule.
    Source homogeneity defaults on; dataset homogeneity defaults off because
    providers can version hourly files independently. Empty input is a warning.
    """
    instrument = Instrument.model_validate(instrument)
    options = ValidationOptions.model_validate(options or ValidationOptions())
    records = tuple(observations)
    errors: list[QualityIssue] = []
    warnings: list[QualityIssue] = []
    seen_times: set[datetime] = set()
    seen_records: set[Observation] = set()
    times: list[datetime] = []
    previous: Observation | None = None
    first: Observation | None = None
    duplicates = duplicate_times = out_of_order = non_monotonic = gaps = 0
    if not records:
        warnings.append(QualityIssue(code="empty", detail="dataset contains no observations"))
    for index, candidate in enumerate(records):
        if type(candidate) not in (MarketQuote, MarketBar):
            errors.append(QualityIssue(code="invalid_record", index=index,
                                       detail="canonical observation required"))
            continue
        try:
            record = type(candidate).model_validate(candidate)
        except ValidationError as exc:
            errors.append(QualityIssue(code="invalid_record", index=index, detail=str(exc)))
            continue
        if first is None:
            first = record
        if type(record) is not type(first):
            errors.append(QualityIssue(code="mixed_kind", index=index,
                                       detail="quotes and bars cannot share a dataset"))
        if record.instrument_id != instrument.instrument_id:
            errors.append(QualityIssue(code="instrument", index=index,
                                       detail="instrument identity mismatch"))
        expected_source = options.expected_source_id
        if expected_source is None and options.homogeneous_source:
            expected_source = first.source_id
        if expected_source is not None and record.source_id != expected_source:
            errors.append(QualityIssue(code="source", index=index, detail="source identity mismatch"))
        expected_dataset = options.expected_dataset_id
        if expected_dataset is None and options.homogeneous_dataset:
            expected_dataset = first.dataset_id
        if expected_dataset is not None and record.dataset_id != expected_dataset:
            errors.append(QualityIssue(code="dataset", index=index, detail="dataset identity mismatch"))
        start = _event_time(record)
        times.append(start)
        if record in seen_records:
            duplicates += 1
            errors.append(QualityIssue(code="exact_duplicate", index=index, detail=start.isoformat()))
        seen_records.add(record)
        if start in seen_times:
            duplicate_times += 1
            errors.append(QualityIssue(code="duplicate", index=index, detail=start.isoformat()))
        seen_times.add(start)
        if previous is not None:
            delta = start - _event_time(previous)
            if delta <= timedelta(0):
                non_monotonic += 1
            if delta < timedelta(0):
                out_of_order += 1
                errors.append(QualityIssue(code="ordering", index=index, detail="event times decreased"))
            if options.max_gap is not None and delta > options.max_gap:
                gaps += 1
                errors.append(QualityIssue(code="gap", index=index,
                                           detail=f"consecutive event gap {delta} exceeds {options.max_gap}"))
            if isinstance(record, MarketBar) and isinstance(previous, MarketBar):
                if start < previous.end_time:
                    errors.append(QualityIssue(code="overlap", index=index,
                                               detail="bar intervals overlap"))
        previous = record
        if isinstance(record, MarketBar) and isinstance(first, MarketBar):
            if (record.timeframe, record.price_type) != (first.timeframe, first.price_type):
                errors.append(QualityIssue(code="mixed_series", index=index,
                                           detail="timeframe or price basis differs"))
    if expected_starts is not None:
        expected = {_as_utc(t) for t in expected_starts}
        for missing in sorted(expected - seen_times):
            gaps += 1
            errors.append(QualityIssue(code="gap", detail=missing.isoformat()))
        for unexpected in sorted(seen_times - expected):
            errors.append(QualityIssue(code="unexpected_time", detail=unexpected.isoformat()))
    return DataQualityReport(
        observation_count=len(records), duplicate_count=duplicates,
        duplicate_timestamp_count=duplicate_times, out_of_order_count=out_of_order,
        non_monotonic_count=non_monotonic, gap_count=gaps,
        first_timestamp=min(times) if times else None,
        last_timestamp=max(times) if times else None,
        warnings=tuple(warnings), errors=tuple(errors),
        gaps_checked=options.max_gap is not None or expected_starts is not None,
    )


class DuplicatePolicy(StrEnum):
    KEEP = "keep"
    REJECT = "reject"
    REMOVE = "remove"


def normalize_observations(
    observations: Iterable[Observation],
    *,
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.REJECT,
) -> tuple[Observation, ...]:
    """Opt-in stable time sort; policy affects exact duplicates only.

    Distinct observations at the same time remain in original relative order.
    Resampling still rejects tied timestamps because no event sequence exists.
    """
    if not isinstance(duplicate_policy, DuplicatePolicy):
        raise ValueError("duplicate_policy must be a DuplicatePolicy member")
    records: list[Observation] = []
    seen: set[Observation] = set()
    for candidate in observations:
        if type(candidate) not in (MarketQuote, MarketBar):
            raise ValueError("canonical observations required")
        record = type(candidate).model_validate(candidate)
        if record in seen:
            if duplicate_policy is DuplicatePolicy.REJECT:
                raise ValueError("exact duplicate observation")
            if duplicate_policy is DuplicatePolicy.REMOVE:
                continue
        seen.add(record)
        records.append(record)
    return tuple(sorted(records, key=_event_time))
