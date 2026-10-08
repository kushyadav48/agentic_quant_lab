"""Explicit errors at the offline paper-kernel boundary."""


class PaperError(ValueError):
    """Base paper input/execution error; never permission to execute."""


class PaperInputError(PaperError):
    """Invalid contract, chronology, unsupported configuration or lifecycle."""


class PaperIdentityConflict(PaperInputError):
    """An existing command/event identity was reused with different content."""


class PaperPricingError(PaperError):
    """Execution economics cannot be represented and reconciled."""
