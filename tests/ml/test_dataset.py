"""Exact joins, explicit windows, label isolation and causal provenance."""
from datetime import timedelta

import pytest
from pydantic import ValidationError
from quantlab.features import FeatureRequest
from quantlab.ml import (ForwardReturnTarget, MLCompatibilityError, MLFeatureSchema,
                         MLInputError, build_dataset, train_model)
from quantlab.validation import ValidationWindow
from tests.backtesting.helpers import D, INSTRUMENT, MINUTE
from .helpers import SCHEMA, dataset, fixture


def test_hand_rows_targets_and_column_order_are_explicit():
    schema = MLFeatureSchema(features=(FeatureRequest(feature_id="z"), FeatureRequest(feature_id="a")))
    series, features = fixture(schema=schema)
    data = dataset(series, features, schema=schema)
    assert schema.feature_ids == ("z", "a")
    assert tuple(r.feature_values for r in data.rows) == ((D(0), D(1)), (D(1), D(2)), (D(2), D(3)))
    assert tuple(r.label.value for r in data.rows) == (D(1), D(2), D(3))
    assert tuple(r.timestamp for r in data.rows) == tuple(b.end_time for b in series[:3])
    assert data.rows[-1].label.target_timestamp == series[3].end_time
    assert data.omitted_label_boundary == 1
    # Equal-time observation enumeration cannot change column order or provenance.
    reversed_ties = tuple(o for i in range(len(series)) for o in reversed(features[i*2:i*2+2]))
    assert dataset(series, reversed_ties, schema=schema) == data


def test_missing_feature_omits_without_imputation():
    series, features = fixture()
    data = dataset(series, features[:1] + features[2:])
    assert tuple(r.feature_values for r in data.rows) == ((D(0),), (D(2),))
    assert data.omitted_missing_features == 1


@pytest.mark.parametrize("delta", [-1, 1])
def test_nearby_timestamp_never_satisfies_exact_join(delta):
    series, features = fixture()
    changed = features[1].model_copy(update={"timestamp": features[1].timestamp + timedelta(seconds=delta),
        "available_at": features[1].available_at + timedelta(seconds=delta)})
    data = dataset(series, features[:1] + (changed,) + features[2:])
    assert data.omitted_missing_features == 1
    assert series[1].end_time not in tuple(r.timestamp for r in data.rows)


def test_unavailable_feature_is_omitted():
    series, features = fixture()
    changed = features[1].model_copy(update={"available_at": series[2].end_time})
    data = dataset(series, features[:1] + (changed,) + features[2:])
    assert data.omitted_unavailable_features == 1


def test_exact_horizon_counts_bars_and_drops_tail():
    data = dataset(end=5, target=ForwardReturnTarget(horizon=2))
    assert tuple(r.label.value for r in data.rows) == (D(5), D(11), D(19))
    assert data.omitted_label_boundary == 2
    assert dataset(end=2, target=ForwardReturnTarget(horizon=5)).rows == ()


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5, "2"])
def test_horizon_is_strict_positive(horizon):
    with pytest.raises(ValidationError):
        ForwardReturnTarget(horizon=horizon)


def test_future_label_information_is_separate_from_inputs():
    series, features = fixture()
    before = dataset(series, features)
    changed = list(series)
    changed[3] = changed[3].model_copy(update={"close": D(48), "high": D(48)})
    after = dataset(changed, features)
    assert tuple(r.feature_values for r in before.rows) == tuple(r.feature_values for r in after.rows)
    assert before.rows[-1].label.value == 3 and after.rows[-1].label.value == 7
    assert train_model(before).coefficients != train_model(after).coefficients


def test_future_bars_and_features_after_cutoff_cannot_change_training():
    series, features = fixture()
    expected = dataset(series, features)
    future_bars = series[:4] + tuple(b.model_copy(update={"high": D(999999), "close": D(999999)}) for b in series[4:])
    future_features = features[:4] + tuple(o.model_copy(update={"value": D(-99999)}) for o in features[4:])
    actual = dataset(future_bars, future_features)
    assert actual == expected
    assert train_model(actual) == train_model(expected)
    assert dataset(series[:4], features[:4]) == expected


def test_explicit_train_window_uses_only_its_rows():
    data = dataset(start=1, end=5)
    assert tuple(r.feature_values for r in data.rows) == ((D(1),), (D(2),), (D(3),))
    assert data.rows[0].timestamp == fixture()[0][1].end_time


def test_delayed_target_is_purged_if_not_known_by_window_end():
    series, features = fixture()
    changed = list(series)
    changed[3] = changed[3].model_copy(update={"available_at": series[4].end_time})
    # Accurate feature availability is required even for an omitted tail feature.
    changed_features = list(features)
    changed_features[3] = changed_features[3].model_copy(update={"available_at": series[4].end_time})
    data = dataset(changed, changed_features)
    assert data.omitted_unavailable_labels == 1 and len(data.rows) == 2
    assert train_model(data).information_cutoff == series[3].end_time


def test_label_availability_within_window_retained_in_provenance():
    series, features = fixture()
    changed = list(series)
    changed[1] = changed[1].model_copy(update={"available_at": series[2].end_time})
    changed_features = list(features)
    changed_features[1] = changed_features[1].model_copy(update={"available_at": series[2].end_time})
    data = dataset(changed, changed_features)
    assert data.rows[0].label.available_at == series[2].end_time
    assert data.omitted_unavailable_features == 1


@pytest.mark.parametrize("kind", ["reverse_bars", "duplicate_bars", "reverse_features", "duplicate_features",
    "instrument", "timeframe", "price_type", "metadata", "undeclared", "uncovered_history", "bar", "feature"])
def test_malformed_input_rejected_before_slicing(kind):
    from quantlab.data import PriceType, Timeframe
    series, features = fixture()
    if kind == "reverse_bars": series = tuple(reversed(series))
    elif kind == "duplicate_bars": series = (series[0],) + series
    elif kind == "reverse_features": features = tuple(reversed(features))
    elif kind == "duplicate_features": features = (features[0],) + features
    elif kind == "bar": series = ({},) + series[1:]
    elif kind == "feature": features = ({},) + features[1:]
    else:
        changes = {"instrument": {"instrument_id": "other"}, "timeframe": {"timeframe": Timeframe.H1},
            "price_type": {"price_type": PriceType.BID}, "metadata": {"implementation_id": "sma"},
            "undeclared": {"feature_id": "other"}, "uncovered_history": {"input_start": series[0].start_time - MINUTE}}
        features = (features[0].model_copy(update=changes[kind]),) + features[1:]
    with pytest.raises((MLInputError, MLCompatibilityError)):
        dataset(series, features)


def test_false_availability_for_delayed_dependency_is_rejected():
    series, features = fixture()
    changed = list(series)
    changed[0] = changed[0].model_copy(update={"available_at": series[2].end_time})
    with pytest.raises(MLInputError, match="contributing"):
        dataset(changed, features)


def test_outside_window_and_timeframe_mismatch():
    with pytest.raises(MLCompatibilityError, match="exceeds"):
        dataset(end=20)
    from quantlab.data import Timeframe
    schema = MLFeatureSchema(features=(FeatureRequest(feature_id="x", timeframe=Timeframe.H1),))
    with pytest.raises(MLCompatibilityError, match="timeframe"):
        dataset(schema=schema)


def test_no_raw_market_columns_in_unlabeled_dataset():
    series, features = fixture()
    before = dataset(series, features, start=4, end=7, target=None)
    changed = tuple(b.model_copy(update={"high": D(999999), "close": D(999999)}) for b in series)
    assert dataset(changed, features, start=4, end=7, target=None) == before
    assert all(r.label is None and len(r.feature_values) == 1 for r in before.rows)


def test_input_lists_and_schema_are_unchanged():
    series, features = map(list, fixture())
    before = tuple(b.model_dump_json() for b in series), tuple(f.model_dump_json() for f in features), SCHEMA.model_dump_json()
    dataset(series, features)
    assert before == (tuple(b.model_dump_json() for b in series), tuple(f.model_dump_json() for f in features), SCHEMA.model_dump_json())
