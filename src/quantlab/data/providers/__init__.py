"""Provider-neutral historical quote interface and ingestion errors.

Concrete adapters are imported explicitly from their own modules.
"""

from .base import (
    HistoricalQuoteProvider,
    HistoricalQuoteRequest,
    ProviderConfigurationError,
    ProviderDataError,
    ProviderTransportError,
)

__all__ = [
    "HistoricalQuoteProvider",
    "HistoricalQuoteRequest",
    "ProviderConfigurationError",
    "ProviderDataError",
    "ProviderTransportError",
]
