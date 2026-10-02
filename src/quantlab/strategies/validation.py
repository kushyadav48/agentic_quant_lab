"""Pure structural and reference checks; never evaluate strategy rules."""
from typing import TYPE_CHECKING
from .enums import Comparison, Direction
if TYPE_CHECKING:
    from .schema import StrategyContent

def validate_content(content: "StrategyContent") -> None:
    from .schema import ConstantOperand, FeatureOperand, ParameterOperand, RiskRewardTarget
    expected = {Direction.LONG: (True, False), Direction.SHORT: (False, True),
                Direction.BOTH: (True, True)}[content.direction]
    if (content.long is not None, content.short is not None) != expected:
        raise ValueError("direction requires exactly its corresponding side rules")
    for label, values in (("instrument", content.instruments),
                          ("feature", tuple(f.feature_id for f in content.features)),
                          ("parameter", tuple(p.name for p in content.parameters))):
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate {label} identifiers")
    features = {f.feature_id for f in content.features}
    parameters = {p.name: p.default for p in content.parameters}
    for side in (content.long, content.short):
        if side is None:
            continue
        for group in (side.entry, side.exit):
            if group is None:
                continue
            for rule in group.rules:
                types = []
                for operand in (rule.left, rule.right):
                    if isinstance(operand, FeatureOperand) and operand.feature_id not in features:
                        raise ValueError("missing referenced feature")
                    if isinstance(operand, ParameterOperand):
                        if operand.name not in parameters:
                            raise ValueError("missing referenced parameter")
                        types.append(type(parameters[operand.name]))
                    elif isinstance(operand, ConstantOperand):
                        types.append(type(operand.value))
                    else:
                        types.append(None)  # market/features are numeric references
                if bool in types:
                    if rule.comparison is not Comparison.EQ or types != [bool, bool]:
                        raise ValueError("boolean operands require equality with another boolean")
    for distance in (content.stop_loss, content.take_profit):
        if distance is not None and hasattr(distance, "feature_id") and distance.feature_id not in features:
            raise ValueError("missing stop/target feature")
    if isinstance(content.take_profit, RiskRewardTarget) and content.stop_loss is None:
        raise ValueError("risk/reward target requires stop loss")
