"""Auditable fitting, prediction, strict models and reproducibility."""
from decimal import Inexact, ROUND_DOWN, localcontext
import socket
import subprocess
import sys

import pytest
from pydantic import ValidationError
from quantlab.features import FeatureObservation, FeatureRequest
from quantlab.ml import (ForwardReturnTarget, MLDataset, MLFeatureSchema, MLInputError,
    MLCompatibilityError, MLModelArtifact, MLModelConfig, MLPrediction, predict_oos,
    prediction_feature_reference, predictions_to_features, train_model)
from tests.backtesting.helpers import D, MINUTE
from .helpers import SCHEMA, artifact, dataset, fixture


def test_exact_ridge_coefficients_and_hand_inference():
    model = train_model(dataset(), MLModelConfig(alpha=D(2)))
    assert model.coefficients == (D("0.5"),) and model.intercept == D("1.5")
    assert model.feature_schema == SCHEMA
    assert model.observation_count == 3
    assert model.train_start == dataset().rows[0].timestamp
    assert model.train_end == dataset().rows[-1].timestamp
    assert model.information_cutoff == fixture()[0][3].end_time
    result = predict_oos(model, dataset(start=4, end=7, target=None))
    assert tuple(p.value for p in result) == (D("3.5"), D("4.0"), D("4.5"))
    assert all(p.available_at == p.timestamp > model.information_cutoff for p in result)
    assert all(p.input_start == model.training_input_start for p in result)


def test_constant_features_have_defined_solution():
    series, features = fixture(values=(5,) * 7)
    model = train_model(dataset(series, features))
    assert model.coefficients == (D(0),) and model.intercept == D(2)


def test_collinear_columns_unique_ridge_solution():
    schema = MLFeatureSchema(features=(FeatureRequest(feature_id="a"), FeatureRequest(feature_id="b")))
    series, features = fixture(schema=schema)
    model = train_model(dataset(series, features, schema=schema), MLModelConfig(alpha=D(4)))
    assert model.coefficients == (D("0.25"), D("0.25")) and model.intercept == D("1.25")


@pytest.mark.parametrize("alpha", [D(0), D(-1), D("NaN"), D("Infinity"), 1.0, True, "1"])
def test_strict_positive_ridge_penalty(alpha):
    with pytest.raises(ValidationError):
        MLModelConfig(alpha=alpha)


@pytest.mark.parametrize("end", [1, 2])
def test_insufficient_labeled_rows(end):
    with pytest.raises(MLCompatibilityError, match="feature count"):
        train_model(dataset(end=end))


def test_training_rejects_unlabeled_and_unknown_models():
    with pytest.raises(MLCompatibilityError, match="labeled"):
        train_model(dataset(target=None))
    with pytest.raises(MLInputError):
        train_model(dataset(), MLModelConfig.model_construct(model_type="forest", alpha=D(1)))


def test_replay_digest_and_provenance_changes():
    model = artifact()
    assert artifact() == model and artifact().model_digest == model.model_digest
    assert len(model.model_digest) == 64
    assert model.training_data_digest == dataset().dataset_digest
    assert train_model(dataset(), MLModelConfig(alpha=D(2))).model_digest != model.model_digest
    # Decimal formatting is not economic content, and does not change identity.
    assert train_model(dataset(), MLModelConfig(alpha=D("1.00"))).model_digest == model.model_digest
    changed = model.model_copy(update={"intercept": model.intercept + 1})
    assert changed.model_digest != model.model_digest


def test_schema_reordering_rejected_even_with_same_ids():
    schema = MLFeatureSchema(features=(FeatureRequest(feature_id="a"), FeatureRequest(feature_id="b")))
    series, features = fixture(schema=schema)
    model = train_model(dataset(series, features, schema=schema))
    wrong = MLFeatureSchema(features=tuple(reversed(schema.features)))
    with pytest.raises(MLCompatibilityError, match="schema"):
        predict_oos(model, dataset(series, features, start=4, end=7, target=None, schema=wrong))


def test_same_alias_changed_implementation_or_parameters_rejected():
    data = dataset(start=4, end=7, target=None)
    wrong = MLFeatureSchema(features=(FeatureRequest(feature_id="x", implementation_id="open"),))
    with pytest.raises(MLCompatibilityError, match="schema"):
        predict_oos(artifact(), data.model_copy(update={"feature_schema": wrong}))


@pytest.mark.parametrize("start,end", [(0, 3), (1, 4), (3, 5)])
def test_at_or_before_information_cutoff_rejected(start, end):
    with pytest.raises(MLCompatibilityError, match="cutoff"):
        predict_oos(artifact(), dataset(start=start, end=end, target=None))


def test_empty_overlapping_window_cannot_hide_time_travel():
    series, _ = fixture()
    empty = dataset(series, (), start=0, end=4, target=None)
    with pytest.raises(MLCompatibilityError, match="cutoff"):
        predict_oos(artifact(), empty)


def test_labeled_oos_input_rejected():
    with pytest.raises(MLCompatibilityError, match="unlabeled"):
        predict_oos(artifact(), dataset(start=4, end=7))


def test_future_feature_changes_do_not_change_prediction_prefix_or_artifact():
    model = artifact()
    series, features = fixture()
    expected = predict_oos(model, dataset(start=4, end=7, target=None))
    changed = features[:-1] + (features[-1].model_copy(update={"value": D("1e100")}),)
    actual = predict_oos(model, dataset(series, changed, start=4, end=7, target=None))
    assert actual[:-1] == expected[:-1] and actual[-1].value != expected[-1].value
    assert model == artifact()


def test_json_roundtrip_and_frozen_public_models():
    data, model = dataset(), artifact()
    prediction = predict_oos(model, dataset(start=4, end=7, target=None))[0]
    reference = prediction_feature_reference(model, "forecast")
    feature = predictions_to_features((prediction,), artifact=model, feature_id="forecast")[0]
    models = (SCHEMA, ForwardReturnTarget(), MLModelConfig(), data, data.rows[0], data.rows[0].label,
              model, prediction, reference, feature)
    for item in models:
        assert type(item).model_validate_json(item.model_dump_json()) == item
        field = next(iter(type(item).model_fields))
        with pytest.raises(ValidationError, match="frozen"):
            setattr(item, field, getattr(item, field))
    assert MLModelArtifact.model_validate_json(model.model_dump_json()).model_digest == model.model_digest


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-Infinity"), float("nan"), 0.5])
def test_nonfinite_or_float_features_rejected(value):
    series, features = fixture()
    with pytest.raises(MLInputError):
        dataset(series, (features[0].model_copy(update={"value": value}),) + features[1:])


@pytest.mark.parametrize("kind", ["row", "label", "dataset", "config", "artifact", "prediction", "window", "target", "schema"])
def test_unchecked_models_revalidated(kind):
    model, data = artifact(), dataset()
    evaluation = dataset(start=4, end=7, target=None)
    if kind == "row":
        bad = data.rows[0].model_copy(update={"feature_values": (D("NaN"),)})
        call = lambda: train_model(data.model_copy(update={"rows": (bad,) + data.rows[1:]}))
    elif kind == "label":
        label = data.rows[0].label.model_copy(update={"available_at": data.window_end + MINUTE})
        bad = data.rows[0].model_copy(update={"label": label})
        call = lambda: train_model(data.model_copy(update={"rows": (bad,) + data.rows[1:]}))
    elif kind == "dataset":
        call = lambda: train_model(data.model_copy(update={"rows": tuple(reversed(data.rows))}))
    elif kind == "config":
        call = lambda: train_model(data, MLModelConfig.model_construct(alpha=D(0)))
    elif kind == "artifact":
        call = lambda: predict_oos(model.model_copy(update={"coefficients": (D("NaN"),)}), evaluation)
    elif kind == "prediction":
        prediction = predict_oos(model, evaluation)[0].model_copy(update={"timestamp": model.information_cutoff})
        call = lambda: predictions_to_features((prediction,), artifact=model, feature_id="forecast")
    elif kind == "window":
        from quantlab.validation import ValidationWindow
        from quantlab.ml import build_dataset
        from tests.backtesting.helpers import INSTRUMENT
        call = lambda: build_dataset(*fixture(), instrument=INSTRUMENT, feature_schema=SCHEMA,
                                     window=ValidationWindow.model_construct(start=-1, end=4))
    elif kind == "target":
        call = lambda: dataset(target=ForwardReturnTarget.model_construct(horizon=0))
    else:
        call = lambda: dataset(schema=MLFeatureSchema.model_construct(features=()))
    with pytest.raises(MLInputError):
        call()


def test_canonical_feature_exact_metadata_and_digest_binding():
    model = artifact()
    predictions = predict_oos(model, dataset(start=4, end=7, target=None))
    result = predictions_to_features(predictions, artifact=model, feature_id="forecast")
    for p, f in zip(predictions, result):
        assert type(f) is FeatureObservation
        assert f.value == p.value and f.timestamp == p.timestamp and f.available_at == p.available_at
        assert f.input_start == p.input_start and f.implementation_id == "ml_forward_return_v1"
        assert f.parameters[0].name == "model_digest"
        assert f.parameters[0].value == int(model.model_digest, 16)
    before = model.model_dump_json()
    assert predictions == predict_oos(model, dataset(start=4, end=7, target=None))
    assert model.model_dump_json() == before
    with pytest.raises(MLCompatibilityError, match="provenance"):
        predictions_to_features(predictions, artifact=train_model(dataset(), MLModelConfig(alpha=D(2))), feature_id="forecast")
    with pytest.raises(MLInputError, match="chronological"):
        predictions_to_features(tuple(reversed(predictions)), artifact=model, feature_id="forecast")


def test_hostile_decimal_context_does_not_change_any_outputs_or_context():
    data = dataset()
    model = train_model(data)
    evaluation = dataset(start=4, end=7, target=None)
    predictions = predict_oos(model, evaluation)
    with localcontext() as context:
        context.clear_flags()
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.Emax = 5
        context.Emin = -5
        assert dataset() == data and train_model(data) == model
        assert model.model_digest == artifact().model_digest
        assert predict_oos(model, evaluation) == predictions
        assert predictions_to_features(predictions, artifact=model, feature_id="forecast")[0].value == predictions[0].value
        assert context.prec == 2 and context.rounding == ROUND_DOWN and context.traps[Inexact]
        assert not any(context.flags.values())


def test_no_network_during_train_and_predict(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    model = artifact()
    assert predict_oos(model, dataset(start=4, end=7, target=None))


def test_fresh_import_network_isolation():
    script = """
import socket

def denied(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket = denied
socket.create_connection = denied
import quantlab.ml
from tests.ml.helpers import artifact, dataset
quantlab.ml.predict_oos(artifact(), dataset(start=4, end=7, target=None))
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
