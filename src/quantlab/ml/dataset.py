"""Exact causal joins and window-local close-to-close supervised targets."""
from collections.abc import Iterable
from decimal import DecimalException, localcontext

from quantlab._decimal import deterministic_context
from quantlab.data import Instrument, MarketBar, ValidationOptions, validate_dataset
from quantlab.features import FeatureObservation
from quantlab.validation.models import ValidationWindow
from .errors import MLCompatibilityError, MLInputError, MLResearchError
from .models import ForwardReturnTarget, MLDataset, MLDatasetRow, MLFeatureSchema, MLLabel, _digest


def build_dataset(bars: Iterable[MarketBar], features: Iterable[FeatureObservation], *,
                  instrument: Instrument, feature_schema: MLFeatureSchema,
                  window: ValidationWindow, target: ForwardReturnTarget | None = None) -> MLDataset:
    """Window is half-open bar indices; labels may not cross its end.

    Omit in priority order: boundary label, missing feature, unavailable feature,
    unavailable label. Validate the full input before slicing. No sorting/filling.
    Caller-supplied feature values must themselves have been computed causally.
    """
    try:
        instrument = Instrument.model_validate(instrument)
        schema = MLFeatureSchema.model_validate(feature_schema)
        window = ValidationWindow.model_validate(window)
        target = None if target is None else ForwardReturnTarget.model_validate(target)
        records = tuple(bars)
        if not records or any(type(b) is not MarketBar for b in records):
            raise MLInputError("nonempty canonical MarketBar sequence required")
        report = validate_dataset(records, instrument=instrument,
                                  options=ValidationOptions(homogeneous_source=False))
        if not report.valid:
            raise MLInputError(f"invalid bar series: {report.errors}")
        records = tuple(MarketBar.model_validate(b) for b in records)
        if window.end > len(records):
            raise MLCompatibilityError("window exceeds supplied bars")
        first = records[0]
        if any(f.timeframe is not None and f.timeframe is not first.timeframe for f in schema.features):
            raise MLCompatibilityError("feature schema timeframe mismatch")
        declarations = {f.feature_id: f for f in schema.features}
        observations = {}
        previous = None
        by_end = {b.end_time: b for b in records}
        delayed = tuple(b for b in records if b.available_at > b.end_time)
        for candidate in features:
            if type(candidate) is not FeatureObservation:
                raise MLInputError("canonical FeatureObservation required")
            observation = FeatureObservation.model_validate(candidate)
            if previous is not None and observation.timestamp < previous:
                raise MLInputError("feature timestamps must be chronological")
            previous = observation.timestamp
            key = (observation.timestamp, observation.feature_id)
            if key in observations:
                raise MLInputError("duplicate feature observation")
            if (observation.instrument_id, observation.timeframe, observation.price_type) != (
                    instrument.instrument_id, first.timeframe, first.price_type):
                raise MLInputError("feature series mismatch")
            request = declarations.get(observation.feature_id)
            if request is None:
                raise MLInputError("undeclared feature observation")
            if (observation.implementation_id != (request.implementation_id or request.feature_id)
                    or observation.parameters != request.parameters):
                raise MLCompatibilityError("feature declaration metadata mismatch")
            ending = by_end.get(observation.timestamp)
            if observation.input_start < first.start_time:
                raise MLCompatibilityError("supplied bars do not cover feature input_start")
            if ending is not None and observation.input_start > ending.start_time:
                raise MLInputError("feature input_start must include ending bar")
            if any(b.available_at > observation.available_at for b in delayed
                   if observation.input_start <= b.start_time and b.end_time <= observation.timestamp):
                raise MLInputError("feature availability precedes contributing bar")
            observations[key] = observation
        rows = []
        missing = unavailable = boundary = unavailable_label = 0
        cutoff = records[window.end - 1].end_time
        with localcontext(deterministic_context()):
            for index in range(window.start, window.end):
                bar = records[index]
                if target is not None and index + target.horizon >= window.end:
                    boundary += 1
                    continue
                selected = tuple(observations.get((bar.end_time, name)) for name in schema.feature_ids)
                if any(o is None for o in selected):
                    missing += 1
                    continue
                if bar.available_at > bar.end_time or any(o.available_at > bar.end_time for o in selected):
                    unavailable += 1
                    continue
                label = None
                if target is not None:
                    future = records[index + target.horizon]
                    label_available = max(bar.available_at, future.available_at)
                    if label_available > cutoff:
                        unavailable_label += 1
                        continue
                    label = MLLabel(value=future.close / bar.close - 1,
                        target_timestamp=future.end_time, available_at=label_available,
                        source_digest=_digest((bar.model_dump(), future.model_dump())))
                rows.append(MLDatasetRow(instrument_id=bar.instrument_id,
                    timeframe=bar.timeframe, price_type=bar.price_type, timestamp=bar.end_time,
                    available_at=max(o.available_at for o in selected),
                    input_start=min(o.input_start for o in selected),
                    feature_values=tuple(o.value for o in selected), label=label,
                    input_digest=_digest(tuple(o.model_dump() for o in selected))))
        return MLDataset(feature_schema=schema, target=target, instrument_id=instrument.instrument_id,
            timeframe=first.timeframe, price_type=first.price_type, window=window,
            window_start=records[window.start].start_time, window_end=cutoff, rows=tuple(rows),
            omitted_missing_features=missing, omitted_unavailable_features=unavailable,
            omitted_label_boundary=boundary, omitted_unavailable_labels=unavailable_label)
    except MLResearchError:
        raise
    except (ValueError, TypeError, DecimalException) as exc:
        raise MLInputError(f"invalid ML dataset input/calculation: {exc}") from exc
