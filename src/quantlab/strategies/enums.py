"""Bounded strategy vocabulary; no evaluation behavior."""
from enum import StrEnum

class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"

class ApprovalState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"

class Origin(StrEnum):
    MANUAL = "manual"
    NATURAL_LANGUAGE = "natural_language"
    CHART_MULTIMODAL = "chart_multimodal"
    ML = "ml"
    AGENT = "agent"

class Comparison(StrEnum):
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    EQ = "eq"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"

class GroupMode(StrEnum):
    ALL = "all"
    ANY = "any"

class MarketField(StrEnum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    BID = "bid"
    ASK = "ask"

class FeatureType(StrEnum):
    INDICATOR = "indicator"
    ML_SIGNAL = "ml_signal"
    LEVEL = "level"

class ParameterType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"

class DistanceUnit(StrEnum):
    PRICE = "price"
    PERCENT = "percent"

class SignalTiming(StrEnum):
    BAR_CLOSE = "bar_close"

class ExecutionTiming(StrEnum):
    NEXT_BAR_OPEN = "next_bar_open"
