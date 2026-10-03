"""Hand-solvable linear research fixtures using existing canonical bar helpers."""
from quantlab.features import FeatureObservation, FeatureRequest
from quantlab.ml import ForwardReturnTarget, MLFeatureSchema, build_dataset, train_model
from quantlab.validation import ValidationWindow
from tests.backtesting.helpers import D, INSTRUMENT, bars

SCHEMA = MLFeatureSchema(features=(FeatureRequest(feature_id="x", implementation_id="close"),))


def fixture(closes=(1, 2, 6, 24, 120, 720, 5040), values=None, schema=SCHEMA):
    series = bars(closes)
    values = tuple(range(len(series))) if values is None else values
    observations = tuple(FeatureObservation(instrument_id=bar.instrument_id, feature_id=f.feature_id,
        implementation_id=f.implementation_id or f.feature_id, parameters=f.parameters,
        timestamp=bar.end_time, available_at=bar.end_time, input_start=bar.start_time,
        value=D(values[i]) + j, timeframe=bar.timeframe, price_type=bar.price_type)
        for i, bar in enumerate(series) for j, f in enumerate(schema.features))
    return series, observations


def dataset(series=None, observations=None, start=0, end=4, target=ForwardReturnTarget(), schema=SCHEMA):
    default_bars, default_features = fixture()
    return build_dataset(default_bars if series is None else series,
        default_features if observations is None else observations, instrument=INSTRUMENT,
        feature_schema=schema, window=ValidationWindow(start=start, end=end), target=target)


def artifact():
    return train_model(dataset())
