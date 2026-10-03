"""OOS inference and digest-bound canonical research feature adapters."""
from collections.abc import Iterable
from decimal import DecimalException, localcontext

from quantlab._decimal import deterministic_context
from quantlab.features import FeatureObservation, FeatureParameter
from quantlab.strategies import FeatureArgument, FeatureReference, FeatureType
from .errors import MLCompatibilityError, MLInputError, MLResearchError
from .models import MLDataset, MLModelArtifact, MLPrediction

IMPLEMENTATION_ID = "ml_forward_return_v1"


def predict_oos(artifact: MLModelArtifact, dataset: MLDataset) -> tuple[MLPrediction, ...]:
    """Accept only unlabeled exact-schema rows strictly after the training cutoff.

    Also gate the entire requested window, so omitted rows cannot hide a request
    for an overlapping/in-sample period. No estimator state is retained or changed.
    """
    try:
        artifact = MLModelArtifact.model_validate(artifact)
        dataset = MLDataset.model_validate(dataset)
        if dataset.target is not None:
            raise MLCompatibilityError("OOS inference requires an unlabeled dataset")
        if dataset.feature_schema != artifact.feature_schema:
            raise MLCompatibilityError("prediction feature schema must exactly match artifact")
        if (dataset.instrument_id, dataset.timeframe, dataset.price_type) != (
                artifact.instrument_id, artifact.timeframe, artifact.price_type):
            raise MLCompatibilityError("prediction series must match training series")
        if dataset.window_start < artifact.information_cutoff:
            raise MLCompatibilityError("evaluation window starts before training information cutoff")
        predictions = []
        with localcontext(deterministic_context()):
            for row in dataset.rows:
                if row.timestamp <= artifact.information_cutoff:
                    raise MLCompatibilityError("OOS decision must strictly follow training information cutoff")
                value = artifact.intercept
                for coefficient, feature in zip(artifact.coefficients, row.feature_values):
                    value += coefficient * feature
                predictions.append(MLPrediction(model_digest=artifact.model_digest,
                    instrument_id=row.instrument_id, timeframe=row.timeframe, price_type=row.price_type,
                    timestamp=row.timestamp, available_at=row.timestamp,
                    information_cutoff=artifact.information_cutoff,
                    input_start=min(artifact.training_input_start, row.input_start),
                    input_digest=row.input_digest, value=value))
        return tuple(predictions)
    except MLResearchError:
        raise
    except (ValueError, TypeError, DecimalException) as exc:
        raise MLInputError(f"invalid ML prediction input/calculation: {exc}") from exc


def prediction_feature_reference(artifact: MLModelArtifact, feature_id: str) -> FeatureReference:
    """Declaration binds approved strategy content to the complete model digest."""
    try:
        artifact = MLModelArtifact.model_validate(artifact)
        return FeatureReference(feature_id=feature_id, implementation_id=IMPLEMENTATION_ID,
            feature_type=FeatureType.ML_SIGNAL, timeframe=artifact.timeframe,
            parameters=(FeatureArgument(name="model_digest", value=int(artifact.model_digest, 16)),))
    except (ValueError, TypeError) as exc:
        raise MLInputError(f"invalid ML feature declaration: {exc}") from exc


def predictions_to_features(predictions: Iterable[MLPrediction], *, artifact: MLModelArtifact,
                            feature_id: str) -> tuple[FeatureObservation, ...]:
    """Preserve ordering, availability and Decimal values; never execute intent."""
    try:
        artifact = MLModelArtifact.model_validate(artifact)
        reference = prediction_feature_reference(artifact, feature_id)
        parameters = tuple(FeatureParameter(name=p.name, value=p.value) for p in reference.parameters)
        output = []
        previous = None
        for candidate in predictions:
            prediction = MLPrediction.model_validate(candidate)
            if (prediction.model_digest != artifact.model_digest
                    or prediction.information_cutoff != artifact.information_cutoff
                    or prediction.input_start > artifact.training_input_start
                    or (prediction.instrument_id, prediction.timeframe, prediction.price_type) != (
                        artifact.instrument_id, artifact.timeframe, artifact.price_type)):
                raise MLCompatibilityError("prediction provenance does not match artifact")
            if previous is not None and prediction.timestamp <= previous:
                raise MLInputError("predictions must be strictly chronological")
            previous = prediction.timestamp
            output.append(FeatureObservation(feature_id=feature_id,
                implementation_id=IMPLEMENTATION_ID, parameters=parameters,
                instrument_id=prediction.instrument_id, timeframe=prediction.timeframe,
                price_type=prediction.price_type, timestamp=prediction.timestamp,
                available_at=prediction.available_at, input_start=prediction.input_start,
                value=prediction.value))
        return tuple(output)
    except MLResearchError:
        raise
    except (ValueError, TypeError) as exc:
        raise MLInputError(f"invalid ML feature conversion: {exc}") from exc
