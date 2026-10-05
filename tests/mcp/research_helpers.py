"""External JSON fixtures backed by existing deterministic service fixtures."""
from quantlab import ml
from tests.ml.helpers import SCHEMA, artifact, dataset, fixture
from .helpers import valid_backtest_request

NAMES = ["run_holdout", "run_walk_forward", "run_parameter_robustness", "build_ml_dataset",
         "train_ml_model", "predict_ml_oos", "ml_predictions_to_features"]


def requests():
    replay = valid_backtest_request()
    series, features = fixture()
    trained = artifact()
    oos = dataset(start=4, end=7, target=None)
    predictions = ml.predict_oos(trained, oos)
    return {
        "run_holdout": replay | {"split": {"train": {"start": 0, "end": 2},
                                           "test": {"start": 2, "end": 4}}},
        "run_walk_forward": replay | {"walk_forward": {
            "train_size": 2, "test_size": 2, "step_size": 2}},
        "run_parameter_robustness": {
            "candidates": [{"candidate_id": "baseline", "strategy": replay["strategy"]}],
            "bars": replay["bars"], "instrument": replay["instrument"], "config": replay["config"],
            "baseline_candidate_id": "baseline", "window": {"start": 0, "end": 4}},
        "build_ml_dataset": {"bars": [b.model_dump(mode="json") for b in series],
            "features": [f.model_dump(mode="json") for f in features],
            "instrument": replay["instrument"], "feature_schema": SCHEMA.model_dump(mode="json"),
            "window": {"start": 0, "end": 4}, "target": {"kind": "forward_return", "horizon": 1}},
        "train_ml_model": {"dataset": dataset().model_dump(mode="json")},
        "predict_ml_oos": {"artifact": trained.model_dump(mode="json"),
                           "dataset": oos.model_dump(mode="json")},
        "ml_predictions_to_features": {"artifact": trained.model_dump(mode="json"),
            "predictions": [p.model_dump(mode="json") for p in predictions], "feature_id": "forecast"},
    }
