"""Decimal-native formulas under the pipeline's isolated 34-digit context."""
from decimal import Decimal
from collections.abc import Iterator
from .models import FeatureRequest

D = Decimal
# (ending bar index, first contributing bar index, value)
Calculation = tuple[int, int, Decimal]


def simple_returns(closes: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if any(c <= 0 or not c.is_finite() for c in closes):
        raise ValueError("returns require finite positive prices")
    return tuple(closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)))


def calculate(request: FeatureRequest, closes: tuple[Decimal, ...],
              returns: tuple[Decimal, ...]) -> Iterator[Calculation]:
    name = request.implementation_id or request.feature_id
    if name in ("simple_return", "log_return"):
        for i in range(1, len(closes)):
            value = returns[i - 1] if name == "simple_return" else (closes[i] / closes[i - 1]).ln()
            yield i, i - 1, value
        return
    n = request.parameters[0].value
    if name == "sma":
        for i in range(n - 1, len(closes)):
            yield i, i - n + 1, sum(closes[i - n + 1:i + 1], D(0)) / n
    elif name == "ema":
        if len(closes) < n:
            return
        value = sum(closes[:n], D(0)) / n
        yield n - 1, 0, value
        alpha = D(2) / (n + 1)
        for i in range(n, len(closes)):
            value = alpha * closes[i] + (1 - alpha) * value
            yield i, 0, value
    elif name == "rsi":
        if len(closes) <= n:
            return
        deltas = tuple(closes[i] - closes[i - 1] for i in range(1, len(closes)))
        gain = sum((max(d, D(0)) for d in deltas[:n]), D(0)) / n
        loss = sum((max(-d, D(0)) for d in deltas[:n]), D(0)) / n
        for i in range(n, len(closes)):
            if i > n:
                gain = (gain * (n - 1) + max(deltas[i - 1], D(0))) / n
                loss = (loss * (n - 1) + max(-deltas[i - 1], D(0))) / n
            value = D(50) if gain == loss == 0 else D(100) if loss == 0 else D(100) - D(100) / (1 + gain / loss)
            yield i, 0, value
    elif name == "rolling_volatility":
        for i in range(n, len(closes)):
            window = returns[i - n:i]
            mean = sum(window, D(0)) / n
            variance = sum(((r - mean) ** 2 for r in window), D(0)) / n
            yield i, i - n, variance.sqrt()
    else:
        raise ValueError(f"unknown feature: {name}")
