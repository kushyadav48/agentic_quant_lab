"""Sanitized local failures; provider failures retain Phase 13 semantics."""


class InterpretationError(Exception):
    message = "strategy interpretation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class InterpretationInputError(InterpretationError):
    message = "invalid interpretation input"


class InterpretedStrategyError(InterpretationError):
    message = "interpreted strategy violates domain or supported vocabulary contracts"


class InterpretationContractError(InterpretationError):
    message = "interpretation artifact or source evidence is inconsistent"
