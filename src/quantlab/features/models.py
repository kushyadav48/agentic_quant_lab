"""Immutable provider-neutral feature contracts."""
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator
from quantlab.data import PriceType, Timeframe
from quantlab.data.models import Identifier, UtcTimestamp, _DomainModel


class FeatureKind(StrEnum):
    RAW = "raw"
    RETURN = "return"
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"


class FeatureParameter(_DomainModel):
    name: Identifier
    value: int


class FeatureRequest(_DomainModel):
    """Output identity may alias a registered algorithm (e.g. fast_sma)."""
    feature_id: Identifier
    implementation_id: Identifier | None = None
    parameters: tuple[FeatureParameter, ...] = ()
    timeframe: Timeframe | None = None

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        if len({p.name for p in self.parameters}) != len(self.parameters):
            raise ValueError("duplicate feature parameters")
        return self


class FeatureDefinition(_DomainModel):
    feature_id: Identifier
    kind: FeatureKind
    required_fields: tuple[str, ...]
    parameter_name: str | None = None
    parameter_minimum: int | None = None
    warmup_semantics: str
    output_semantics: str
    timeframe: Timeframe | None = None

    def required_bars(self, request: FeatureRequest) -> int:
        """Called on a registry-validated request."""
        n = request.parameters[0].value if request.parameters else 1
        if self.feature_id in ("simple_return", "log_return"):
            return 2
        return n + 1 if self.feature_id in ("rsi", "rolling_volatility") else n


class FeatureObservation(_DomainModel):
    instrument_id: Identifier
    feature_id: Identifier
    timestamp: UtcTimestamp
    available_at: UtcTimestamp
    value: Annotated[Decimal, Field(allow_inf_nan=False)]
    timeframe: Timeframe
    price_type: PriceType
    implementation_id: Identifier
    parameters: tuple[FeatureParameter, ...] = ()
    input_start: UtcTimestamp

    @model_validator(mode="after")
    def causal_times(self) -> Self:
        if self.available_at < self.timestamp:
            raise ValueError("available_at must not precede timestamp")
        if self.input_start >= self.timestamp:
            raise ValueError("input_start must precede timestamp")
        return self
