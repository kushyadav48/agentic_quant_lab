"""Offline research boundary errors."""


class MLResearchError(ValueError):
    """Base error for Phase 12 research operations."""


class MLInputError(MLResearchError):
    """Malformed canonical input or arithmetic outside supported range."""


class MLCompatibilityError(MLResearchError):
    """Valid inputs incompatible with the requested research operation."""
