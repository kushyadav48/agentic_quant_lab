"""Malformed input differs from an ordinary undefined statistic."""


class AnalyticsError(ValueError):
    """Base error for the offline analytics boundary."""


class AnalyticsInputError(AnalyticsError):
    """Invalid records or calculations unrepresentable at precision 34."""
