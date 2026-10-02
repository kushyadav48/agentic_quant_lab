"""Pure batch feature computation on validated chronological canonical bars."""
from collections.abc import Iterable
from decimal import Context, ROUND_HALF_EVEN, localcontext

from quantlab.data import Instrument, MarketBar, ValidationOptions, validate_dataset
from .indicators import Calculation, calculate, simple_returns
from .models import FeatureKind, FeatureObservation, FeatureParameter, FeatureRequest
from .registry import DEFAULT_REGISTRY, FeatureRegistry


def compute_features(bars: Iterable[MarketBar], requested_features: Iterable[FeatureRequest],
                     *, instrument: Instrument,
                     registry: FeatureRegistry = DEFAULT_REGISTRY) -> tuple[FeatureObservation, ...]:
    """Timestamp is final bar end; output order is (timestamp, feature_id).

    Gaps are allowed, never filled: windows count observed bars. Input validation
    reuses Phase 4; instrument metadata is explicit rather than fabricated.
    """
    instrument = Instrument.model_validate(instrument)
    records = tuple(bars)
    if any(type(b) is not MarketBar for b in records):
        raise ValueError("feature inputs must be canonical MarketBar objects")
    report = validate_dataset(records, instrument=instrument,
        options=ValidationOptions(homogeneous_source=False))
    if not report.valid:
        raise ValueError(f"invalid feature input: {report.errors}")
    # Retain revalidated, UTC-normalized copies, including unchecked Pydantic copies.
    records = tuple(MarketBar.model_validate(b) for b in records)
    requests = tuple(FeatureRequest.model_validate(r) for r in requested_features)
    if len({r.feature_id for r in requests}) != len(requests):
        raise ValueError("duplicate output feature identities; use explicit aliases")
    timeframe = records[0].timeframe if records else None
    definitions = tuple(registry.validate(r, timeframe or r.timeframe) for r in requests)
    if not records:
        return ()
    output: list[FeatureObservation] = []
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        closes = tuple(b.close for b in records)
        returns = simple_returns(closes) if any(d.feature_id in ("simple_return", "rolling_volatility") for d in definitions) else ()
        cache: dict[tuple[str, tuple[FeatureParameter, ...]], tuple[Calculation, ...]] = {}
        for request, definition in zip(requests, definitions):
            key = (definition.feature_id, request.parameters)
            if key not in cache:
                cache[key] = (tuple((i, i, getattr(b, definition.feature_id)) for i, b in enumerate(records))
                              if definition.kind is FeatureKind.RAW else tuple(calculate(request, closes, returns)))
            for end, start, value in cache[key]:
                bar = records[end]
                output.append(FeatureObservation(instrument_id=bar.instrument_id,
                    feature_id=request.feature_id, implementation_id=definition.feature_id,
                    parameters=request.parameters, timestamp=bar.end_time,
                    available_at=max(b.available_at for b in records[start:end + 1]),
                    value=value, timeframe=bar.timeframe, price_type=bar.price_type,
                    input_start=records[start].start_time))
    return tuple(sorted(output, key=lambda o: (o.timestamp, o.feature_id)))
