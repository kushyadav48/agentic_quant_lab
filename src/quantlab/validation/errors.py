"""Errors for offline research orchestration."""


class ResearchValidationError(ValueError):
    """Base research-validation failure."""


class ResearchValidationInputError(ResearchValidationError):
    """Malformed data, configuration or candidate collection."""


class ResearchValidationCompatibilityError(ResearchValidationError):
    """Strategy or causal history cannot support the requested experiment."""
