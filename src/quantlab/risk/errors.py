"""Input failures are distinct from valid hard-limit rejections."""


class RiskError(ValueError):
    """Base error for deterministic entry risk evaluation."""


class RiskInputError(RiskError):
    """Malformed risk configuration or entry context."""
