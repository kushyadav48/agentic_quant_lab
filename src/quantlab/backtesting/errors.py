"""Distinguish incompatible intent, invalid data, and conflicting entries."""


class BacktestError(ValueError):
    """Base error for the deterministic research backtester."""


class BacktestCompatibilityError(BacktestError):
    """Strategy intent cannot be simulated by this engine."""


class BacktestInputError(BacktestError):
    """Malformed or inconsistent simulation inputs."""


class BacktestSignalConflictError(BacktestError):
    """Both entry sides became TRUE at the same flat decision."""
