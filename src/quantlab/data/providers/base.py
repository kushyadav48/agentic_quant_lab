"""Small provider-neutral boundary for historical bid/ask observations."""

from collections.abc import Iterator
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ..models import Instrument, MarketQuote, UtcTimestamp


class HistoricalQuoteRequest(BaseModel):
    """Tick quotes for one instrument over the UTC interval [start_time, end_time).

    Python callers must supply aware datetime objects. No bars or aggregation
    granularity are requested: a quote is a single source event.
    """

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )

    instrument: Instrument
    start_time: UtcTimestamp
    end_time: UtcTimestamp

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class HistoricalQuoteProvider(Protocol):
    """Providers yield the same domain quotes; no provider types reach consumers."""

    def fetch(self, request: HistoricalQuoteRequest) -> Iterator[MarketQuote]:
        """Yield available quotes, or no items when data is absent."""
        ...


class ProviderConfigurationError(ValueError):
    """An instrument or provider setting is not supported."""


class ProviderDataError(ValueError):
    """A nonempty provider payload or decoded observation is malformed."""


class ProviderTransportError(RuntimeError):
    """HTTP/network failure, distinct from a successfully identified missing file."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
