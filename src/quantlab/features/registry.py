"""Fixed declarations and structural compatibility; no dynamic plugins."""
from types import MappingProxyType
from collections.abc import Mapping
from quantlab.data import Timeframe
from quantlab.strategies import FeatureReference, FeatureType, StrategySpecification
from .models import FeatureDefinition, FeatureKind, FeatureParameter, FeatureRequest


def _definition(name: str, kind: FeatureKind, parameter: str | None = None,
                minimum: int | None = None, warmup: str = "1 bar",
                output: str = "quoted price") -> FeatureDefinition:
    return FeatureDefinition(feature_id=name, kind=kind,
        required_fields=(name,) if kind is FeatureKind.RAW else ("close",),
        parameter_name=parameter, parameter_minimum=minimum,
        warmup_semantics=warmup, output_semantics=output)


_DEFINITIONS = MappingProxyType({d.feature_id: d for d in (
    *(_definition(n, FeatureKind.RAW) for n in ("open", "high", "low", "close")),
    _definition("simple_return", FeatureKind.RETURN, warmup="2 bars", output="fractional return"),
    _definition("log_return", FeatureKind.RETURN, warmup="2 bars", output="natural log return"),
    _definition("sma", FeatureKind.TREND, "period", 1, "period bars"),
    _definition("ema", FeatureKind.TREND, "period", 1, "period bars; SMA seed"),
    _definition("rsi", FeatureKind.MOMENTUM, "period", 1, "period + 1 bars", "index in [0, 100]"),
    _definition("rolling_volatility", FeatureKind.VOLATILITY, "window", 2,
                "window + 1 bars", "population std-dev of simple returns"),
)})


class FeatureRegistry:
    """Read-only supported algorithms; parameters never acquire implicit defaults."""
    @property
    def definitions(self) -> Mapping[str, FeatureDefinition]:
        return _DEFINITIONS

    def get(self, feature_id: str) -> FeatureDefinition:
        try:
            return _DEFINITIONS[feature_id]
        except KeyError:
            raise ValueError(f"unknown feature: {feature_id}") from None

    def validate(self, request: FeatureRequest, timeframe: Timeframe | None) -> FeatureDefinition:
        request = FeatureRequest.model_validate(request)
        definition = self.get(request.implementation_id or request.feature_id)
        names = tuple(p.name for p in request.parameters)
        expected = (definition.parameter_name,) if definition.parameter_name else ()
        if names != expected:
            raise ValueError(f"{definition.feature_id} requires exactly parameters {expected}")
        if expected and request.parameters[0].value < definition.parameter_minimum:
            raise ValueError(f"{definition.parameter_name} must be >= {definition.parameter_minimum}")
        if timeframe is not None and request.timeframe is not None and request.timeframe is not timeframe:
            raise ValueError("timeframe override requires a separate matching bar series")
        if definition.timeframe is not None and definition.timeframe is not timeframe:
            raise ValueError("unsupported feature timeframe")
        return definition


DEFAULT_REGISTRY = FeatureRegistry()


def validate_feature_reference(reference: FeatureReference, *, timeframe: Timeframe,
                               registry: FeatureRegistry = DEFAULT_REGISTRY) -> FeatureRequest:
    reference = FeatureReference.model_validate(reference)
    if reference.feature_type is FeatureType.ML_SIGNAL:
        # Externally fitted Phase 12 outputs are declarations, not bar indicators.
        # The integer parameter losslessly binds the complete 256-bit model digest.
        if reference.implementation_id != "ml_forward_return_v1":
            raise ValueError("unsupported ML implementation")
        if (len(reference.parameters) != 1 or reference.parameters[0].name != "model_digest"
                or type(reference.parameters[0].value) is not int
                or not 0 <= reference.parameters[0].value < 2**256):
            raise ValueError("ML feature requires one unsigned 256-bit model_digest parameter")
        if reference.timeframe is not None and reference.timeframe is not timeframe:
            raise ValueError("ML feature timeframe must match strategy")
        return FeatureRequest(feature_id=reference.feature_id,
            implementation_id=reference.implementation_id, timeframe=reference.timeframe,
            parameters=(FeatureParameter(name="model_digest", value=reference.parameters[0].value),))
    if reference.feature_type is not FeatureType.INDICATOR:
        raise ValueError("registered features require INDICATOR feature_type")
    request = FeatureRequest(feature_id=reference.feature_id,
        implementation_id=reference.implementation_id, timeframe=reference.timeframe,
        parameters=tuple(FeatureParameter(name=p.name, value=p.value) for p in reference.parameters))
    registry.validate(request, timeframe)
    return request


def validate_strategy_features(specification: StrategySpecification, *,
        registry: FeatureRegistry = DEFAULT_REGISTRY) -> tuple[FeatureRequest, ...]:
    """Validate feature declarations only; never evaluate rules or promote approval."""
    specification = StrategySpecification.model_validate(specification)
    return tuple(validate_feature_reference(f, timeframe=specification.content.timeframe,
                 registry=registry) for f in specification.content.features)
