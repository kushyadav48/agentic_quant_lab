"""Small deterministic simulation vocabulary."""
from enum import StrEnum


class EvaluationResult(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNAVAILABLE = "unavailable"


class SignalAction(StrEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"
