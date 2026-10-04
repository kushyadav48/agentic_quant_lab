"""Versioned interpretation allowlist; declarations only, no feature calculation.

Kept local to avoid importing computational layers. Offline parity tests bind this
policy to the existing feature registry and its ML compatibility helper.
"""
from quantlab.strategies import FeatureType, StrategyContent

# (implementation_id, required integer argument, minimum). No parameter defaults.
INDICATORS = (
    ("open", None, None), ("high", None, None), ("low", None, None), ("close", None, None),
    ("simple_return", None, None), ("log_return", None, None),
    ("sma", "period", 1), ("ema", "period", 1), ("rsi", "period", 1),
    ("rolling_volatility", "window", 2),
)
ML_IMPLEMENTATION = "ml_forward_return_v1"


def validate_vocabulary(content: StrategyContent) -> None:
    """Structural support checks only; READY never establishes execution eligibility."""
    for feature in content.features:
        if feature.timeframe is not None and feature.timeframe is not content.timeframe:
            raise ValueError("cross-timeframe features are unsupported")
        if feature.feature_type is FeatureType.ML_SIGNAL:
            if feature.implementation_id != ML_IMPLEMENTATION:
                raise ValueError("unsupported ML implementation")
            args = feature.parameters
            if (len(args) != 1 or args[0].name != "model_digest"
                    or type(args[0].value) is not int or not 0 <= args[0].value < 2**256):
                raise ValueError("ML requires an explicit unsigned 256-bit model digest")
            continue
        if feature.feature_type is not FeatureType.INDICATOR:
            raise ValueError("unsupported feature category")
        implementation = feature.implementation_id or feature.feature_id
        definition = next((d for d in INDICATORS if d[0] == implementation), None)
        if definition is None:
            raise ValueError("unsupported feature implementation")
        _, argument, minimum = definition
        args = feature.parameters
        if argument is None:
            if args:
                raise ValueError("feature takes no arguments")
        elif (len(args) != 1 or args[0].name != argument
              or type(args[0].value) is not int or args[0].value < minimum):
            raise ValueError("invalid feature arguments")
