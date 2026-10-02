"""Pure tri-state evaluation over exact bar-relative observations."""
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

from quantlab.data import MarketBar
from quantlab.features import FeatureObservation
from quantlab.strategies import (
    Comparison, ConstantOperand, FeatureOperand, GroupMode, MarketField,
    MarketOperand, ParameterOperand, Rule, RuleGroup, StrategySpecification,
)
from quantlab.strategies.schema import Operand, Scalar
from .enums import EvaluationResult as E
from .errors import BacktestCompatibilityError, BacktestInputError


class RuleEvaluator:
    """Low-level evaluator for inputs validated by run_backtest.

    Offsets count observed bars. All dependencies are checked against the current
    decision time, including the prior values used in a crossing. No lookup uses
    nearest values, forward filling, or future observations.
    """
    def __init__(self, strategy: StrategySpecification, bars: tuple[MarketBar, ...],
                 features: tuple[FeatureObservation, ...] = ()) -> None:
        self._bars = bars
        self._features: Mapping[tuple[str, datetime], FeatureObservation] = MappingProxyType(
            {(o.feature_id, o.timestamp): o for o in features})
        self._parameters: Mapping[str, Scalar] = MappingProxyType(
            {p.name: p.default for p in strategy.content.parameters})

    def _resolve(self, operand: Operand, index: int, decision_time: datetime) -> Scalar | None:
        if index < 0:
            return None
        if isinstance(operand, ConstantOperand):
            return operand.value
        if isinstance(operand, ParameterOperand):
            return self._parameters[operand.name]
        target = index - operand.offset
        if target < 0:
            return None
        bar = self._bars[target]
        if isinstance(operand, MarketOperand):
            if operand.field in (MarketField.BID, MarketField.ASK):
                raise BacktestCompatibilityError("BID/ASK operands require quotes, not MarketBar")
            if bar.available_at > decision_time:
                return None
            return getattr(bar, operand.field.value)
        if isinstance(operand, FeatureOperand):
            observation = self._features.get((operand.feature_id, bar.end_time))
            if observation is None or observation.available_at > decision_time:
                return None
            return observation.value
        raise BacktestCompatibilityError("unsupported operand")

    def _rule(self, rule: Rule, index: int, decision_time: datetime) -> E:
        left = self._resolve(rule.left, index, decision_time)
        right = self._resolve(rule.right, index, decision_time)
        if left is None or right is None:
            return E.UNAVAILABLE
        comparison = rule.comparison
        if type(left) is bool or type(right) is bool:
            if comparison is not Comparison.EQ or type(left) is not bool or type(right) is not bool:
                raise BacktestCompatibilityError("boolean operands require boolean equality")
        if comparison in (Comparison.CROSSES_ABOVE, Comparison.CROSSES_BELOW):
            previous_left = self._resolve(rule.left, index - 1, decision_time)
            previous_right = self._resolve(rule.right, index - 1, decision_time)
            if previous_left is None or previous_right is None:
                return E.UNAVAILABLE
            matched = (left > right and previous_left <= previous_right
                       if comparison is Comparison.CROSSES_ABOVE
                       else left < right and previous_left >= previous_right)
        elif comparison is Comparison.GT:
            matched = left > right
        elif comparison is Comparison.GE:
            matched = left >= right
        elif comparison is Comparison.LT:
            matched = left < right
        elif comparison is Comparison.LE:
            matched = left <= right
        elif comparison is Comparison.EQ:
            matched = left == right
        else:
            raise BacktestCompatibilityError("unsupported comparison")
        return E.TRUE if matched else E.FALSE

    def evaluate(self, rule: Rule | RuleGroup, index: int) -> E:
        """Evaluate at this bar close; missing information is distinct from FALSE."""
        if type(index) is not int or not 0 <= index < len(self._bars):
            raise BacktestInputError("evaluation index must identify an input bar")
        bar = self._bars[index]
        if bar.available_at > bar.end_time:
            return E.UNAVAILABLE
        if isinstance(rule, Rule):
            return self._rule(rule, index, bar.end_time)
        results = tuple(self._rule(child, index, bar.end_time) for child in rule.rules)
        if rule.mode is GroupMode.ALL:
            if E.FALSE in results:
                return E.FALSE
            return E.UNAVAILABLE if E.UNAVAILABLE in results else E.TRUE
        if E.TRUE in results:
            return E.TRUE
        return E.UNAVAILABLE if E.UNAVAILABLE in results else E.FALSE
