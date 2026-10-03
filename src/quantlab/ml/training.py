"""Small deterministic ridge baseline with exact rational normal equations."""
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction

from quantlab._decimal import deterministic_context
from .errors import MLCompatibilityError, MLInputError, MLResearchError
from .models import MLDataset, MLModelArtifact, MLModelConfig


def _solve(matrix, rhs):
    """Exact elimination of a positive-definite ridge system; no tolerance/seed."""
    size = len(rhs)
    augmented = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for column in range(size):
        pivot = augmented[column][column]
        if not pivot:
            raise MLCompatibilityError("singular ridge system")
        for row in range(column + 1, size):
            factor = augmented[row][column] / pivot
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]
    result = [Fraction(0)] * size
    for row in range(size - 1, -1, -1):
        result[row] = (augmented[row][-1] - sum(augmented[row][j] * result[j]
                       for j in range(row + 1, size))) / augmented[row][row]
    return result


def _decimal(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def train_model(dataset: MLDataset, config: MLModelConfig | None = None) -> MLModelArtifact:
    """Minimize sum squared error + alpha * sum squared slopes; free intercept.

    At least k+1 labeled rows for k declared features. Positive alpha defines a
    unique solution even for constant/collinear columns. No scaling or selection.
    Exact rational fitting is intended for small offline research datasets.
    """
    try:
        dataset = MLDataset.model_validate(dataset)
        config = MLModelConfig() if config is None else MLModelConfig.model_validate(config)
        if dataset.target is None:
            raise MLCompatibilityError("training requires a labeled dataset")
        rows = dataset.rows
        width = len(dataset.feature_schema.features)
        count = len(rows)
        if count < width + 1:
            raise MLCompatibilityError("ridge requires at least feature count + 1 labeled rows")
        x = tuple(tuple(Fraction(v) for v in row.feature_values) for row in rows)
        y = tuple(Fraction(row.label.value) for row in rows)
        means = tuple(sum(row[j] for row in x) / count for j in range(width))
        mean_y = sum(y) / count
        centered = tuple(tuple(row[j] - means[j] for j in range(width)) for row in x)
        matrix = [[sum(row[j] * row[k] for row in centered)
                   + (Fraction(config.alpha) if j == k else 0)
                   for k in range(width)] for j in range(width)]
        rhs = [sum(row[j] * (value - mean_y) for row, value in zip(centered, y))
               for j in range(width)]
        slopes = _solve(matrix, rhs)
        intercept = mean_y - sum(slope * mean for slope, mean in zip(slopes, means))
        with localcontext(deterministic_context()):
            return MLModelArtifact(config=config, feature_schema=dataset.feature_schema,
                target=dataset.target, instrument_id=dataset.instrument_id,
                timeframe=dataset.timeframe, price_type=dataset.price_type,
                training_window=dataset.window, train_start=rows[0].timestamp,
                train_end=rows[-1].timestamp, training_input_start=min(r.input_start for r in rows),
                information_cutoff=dataset.window_end, observation_count=count,
                training_data_digest=dataset.dataset_digest,
                coefficients=tuple(_decimal(v) for v in slopes), intercept=_decimal(intercept))
    except MLResearchError:
        raise
    except (ValueError, TypeError, DecimalException, OverflowError) as exc:
        raise MLInputError(f"invalid ML training input/calculation: {exc}") from exc
