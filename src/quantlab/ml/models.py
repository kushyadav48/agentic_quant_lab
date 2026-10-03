"""Strict immutable research contracts; labels are never feature columns."""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from quantlab.data import PriceType, Timeframe
from quantlab.data.models import Identifier, PositiveDecimal, UtcTimestamp, _DomainModel
from quantlab.features import FeatureRequest
from quantlab.strategies.schema import Digest
from quantlab.validation.models import ValidationWindow

FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]


def _canonical(value):
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _digest(value) -> str:
    wire = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


class MLFeatureSchema(_DomainModel):
    """Ordered explicit declarations, including implementation and parameters."""
    features: tuple[FeatureRequest, ...] = Field(min_length=1)

    @property
    def feature_ids(self) -> tuple[str, ...]:
        return tuple(f.feature_id for f in self.features)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.feature_ids)) != len(self.features):
            raise ValueError("feature identifiers must be unique")
        return self


class ForwardReturnTarget(_DomainModel):
    kind: Literal["forward_return"] = "forward_return"
    horizon: Annotated[int, Field(gt=0)] = 1


class MLLabel(_DomainModel):
    value: FiniteDecimal
    target_timestamp: UtcTimestamp
    available_at: UtcTimestamp
    source_digest: Digest

    @model_validator(mode="after")
    def causal(self) -> Self:
        if self.available_at < self.target_timestamp:
            raise ValueError("label availability precedes target bar close")
        return self


class MLDatasetRow(_DomainModel):
    instrument_id: Identifier
    timeframe: Timeframe
    price_type: PriceType
    timestamp: UtcTimestamp
    available_at: UtcTimestamp
    input_start: UtcTimestamp
    feature_values: tuple[FiniteDecimal, ...] = Field(min_length=1)
    input_digest: Digest
    label: MLLabel | None = None

    @model_validator(mode="after")
    def causal(self) -> Self:
        if self.available_at != self.timestamp or self.input_start >= self.timestamp:
            raise ValueError("row inputs must be known at exact decision close")
        if self.label is not None and self.label.target_timestamp <= self.timestamp:
            raise ValueError("forward label must follow decision close")
        return self


class MLDataset(_DomainModel):
    definition_version: Literal["phase12-v1"] = "phase12-v1"
    feature_schema: MLFeatureSchema
    target: ForwardReturnTarget | None = None
    instrument_id: Identifier
    timeframe: Timeframe
    price_type: PriceType
    window: ValidationWindow
    window_start: UtcTimestamp
    window_end: UtcTimestamp
    rows: tuple[MLDatasetRow, ...]
    omitted_missing_features: Count = 0
    omitted_unavailable_features: Count = 0
    omitted_label_boundary: Count = 0
    omitted_unavailable_labels: Count = 0

    @property
    def dataset_digest(self) -> str:
        return _digest(self.model_dump(mode="python"))

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.window_start >= self.window_end:
            raise ValueError("invalid dataset time bounds")
        if any(f.timeframe is not None and f.timeframe is not self.timeframe
               for f in self.feature_schema.features):
            raise ValueError("schema timeframe mismatch")
        counts = (self.omitted_missing_features, self.omitted_unavailable_features,
                  self.omitted_label_boundary, self.omitted_unavailable_labels)
        if len(self.rows) + sum(counts) != self.window.end - self.window.start:
            raise ValueError("rows and omissions must account for window")
        if self.target is None and any(counts[2:]):
            raise ValueError("inference datasets cannot omit labels")
        previous = None
        for row in self.rows:
            if (row.instrument_id, row.timeframe, row.price_type) != (
                    self.instrument_id, self.timeframe, self.price_type):
                raise ValueError("mixed row series")
            if len(row.feature_values) != len(self.feature_schema.features):
                raise ValueError("row width must match ordered schema")
            if not self.window_start < row.timestamp <= self.window_end:
                raise ValueError("row outside dataset window")
            if previous is not None and row.timestamp <= previous:
                raise ValueError("rows must be strictly chronological")
            previous = row.timestamp
            if (row.label is None) != (self.target is None):
                raise ValueError("dataset target and row labels must agree")
            if row.label is not None and row.label.available_at > self.window_end:
                raise ValueError("label information exceeds training window")
        return self


class MLModelConfig(_DomainModel):
    model_type: Literal["ridge"] = "ridge"
    alpha: PositiveDecimal = Decimal("1")


class MLModelArtifact(_DomainModel):
    definition_version: Literal["phase12-v1"] = "phase12-v1"
    config: MLModelConfig
    feature_schema: MLFeatureSchema
    target: ForwardReturnTarget
    instrument_id: Identifier
    timeframe: Timeframe
    price_type: PriceType
    training_window: ValidationWindow
    train_start: UtcTimestamp
    train_end: UtcTimestamp
    training_input_start: UtcTimestamp
    information_cutoff: UtcTimestamp
    observation_count: Annotated[int, Field(gt=0)]
    training_data_digest: Digest
    coefficients: tuple[FiniteDecimal, ...]
    intercept: FiniteDecimal

    @property
    def model_digest(self) -> str:
        return _digest(self.model_dump(mode="python"))

    @model_validator(mode="after")
    def coherent(self) -> Self:
        width = len(self.feature_schema.features)
        if len(self.coefficients) != width:
            raise ValueError("coefficient count must match ordered schema")
        if not width + 1 <= self.observation_count <= self.training_window.end - self.training_window.start:
            raise ValueError("ridge requires at least feature count + 1 labeled rows")
        if not self.training_input_start < self.train_start < self.train_end < self.information_cutoff:
            raise ValueError("training cutoff must include future label information")
        if any(f.timeframe is not None and f.timeframe is not self.timeframe
               for f in self.feature_schema.features):
            raise ValueError("schema timeframe mismatch")
        return self


class MLPrediction(_DomainModel):
    model_digest: Digest
    instrument_id: Identifier
    timeframe: Timeframe
    price_type: PriceType
    timestamp: UtcTimestamp
    available_at: UtcTimestamp
    information_cutoff: UtcTimestamp
    input_start: UtcTimestamp
    input_digest: Digest
    value: FiniteDecimal

    @model_validator(mode="after")
    def causal(self) -> Self:
        if not self.input_start < self.information_cutoff < self.timestamp:
            raise ValueError("OOS decision must strictly follow training information cutoff")
        if self.available_at != self.timestamp:
            raise ValueError("prediction availability must equal its causal decision close")
        return self
