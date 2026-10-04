"""Public provider-neutral market-data contracts."""

from importlib import import_module

from .enums import AssetClass, PriceType, Timeframe, VolumeType
from .models import Instrument, MarketBar, MarketQuote, TradingCalendar

# Importing the shared Timeframe enum must not load storage or calculation
# engines into interpretation. Public service exports retain their exact objects
# and are loaded only when requested by a data-service caller.
_SERVICE_MODULES = {
    **dict.fromkeys(("MissingDataPolicy", "ResampleRequest", "resample"), "resampling"),
    **dict.fromkeys(("DatasetMetadata", "DatasetStore", "ObservationType", "SQLiteDatasetStore",
                    "StoredDataset", "StorageIntegrityError"), "storage"),
    **dict.fromkeys(("DataQualityReport", "DuplicatePolicy", "QualityIssue", "QualityReport",
                    "ValidationOptions", "normalize_observations", "validate_dataset"), "validation"),
}


def __getattr__(name: str):
    module = _SERVICE_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "DataQualityReport",
    "DuplicatePolicy",
    "ObservationType",
    "ValidationOptions",
    "normalize_observations",
    "DatasetMetadata",
    "DatasetStore",
    "MissingDataPolicy",
    "QualityIssue",
    "QualityReport",
    "ResampleRequest",
    "SQLiteDatasetStore",
    "StorageIntegrityError",
    "StoredDataset",
    "resample",
    "validate_dataset",
    "AssetClass",
    "Instrument",
    "MarketBar",
    "MarketQuote",
    "PriceType",
    "Timeframe",
    "TradingCalendar",
    "VolumeType",
]
