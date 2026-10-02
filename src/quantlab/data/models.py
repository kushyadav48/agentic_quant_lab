"""Strict domain contracts only: no providers, ingestion, or execution logic."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .enums import AssetClass, PriceType, Timeframe, VolumeType

Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S+$")]
CurrencyCode = Annotated[
    str, StringConstraints(min_length=3, max_length=12, pattern=r"^[A-Z][A-Z0-9]+$")
]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


UtcTimestamp = Annotated[datetime, AfterValidator(_as_utc)]


class _DomainModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        revalidate_instances="always",
    )


class TradingCalendar(_DomainModel):
    """Descriptive calendar reference; no session schedule or timezone resolution.

    timezone is an IANA name supplied by the caller (for example America/New_York).
    Its resolution and actual holiday/session rules belong to a future calendar
    adapter. Neither this reference nor a timeframe implies continuous trading.
    """

    calendar_id: Identifier
    timezone: Identifier


class Instrument(_DomainModel):
    """Market-neutral metadata with required Forex conventions when applicable.

    instrument_id is the caller's stable domain identity, not a provider symbol.
    tick_size is in quote currency; quantity_increment and Forex lot_size are in
    base units. contract_multiplier describes the contract's valuation scale.
    Forex pip_size is in quote currency. No sizing or valuation is computed here.
    Currency labels are uppercase codes, not a registry of recognized currencies.
    """

    instrument_id: Identifier
    symbol: Identifier
    asset_class: AssetClass
    quote_currency: CurrencyCode
    tick_size: PositiveDecimal
    quantity_increment: PositiveDecimal
    calendar: TradingCalendar
    contract_multiplier: PositiveDecimal = Decimal("1")
    base_currency: CurrencyCode | None = None
    pip_size: PositiveDecimal | None = None
    lot_size: PositiveDecimal | None = None

    @model_validator(mode="after")
    def validate_market_metadata(self) -> Self:
        if self.base_currency == self.quote_currency:
            raise ValueError("base_currency and quote_currency must differ")
        if self.asset_class is AssetClass.FOREX:
            if self.base_currency is None or self.pip_size is None or self.lot_size is None:
                raise ValueError("Forex requires base_currency, pip_size, and lot_size")
            for code in (self.base_currency, self.quote_currency):
                if len(code) != 3 or not code.isalpha():
                    raise ValueError("Forex currency codes must contain three uppercase letters")
            if self.pip_size < self.tick_size:
                raise ValueError("Forex pip_size must be at least tick_size")
        elif self.pip_size is not None or self.lot_size is not None:
            raise ValueError("pip_size and lot_size are Forex-specific metadata")
        return self


class _Observation(_DomainModel):
    """Domain identifiers for the instrument and originating source/dataset.

    These are opaque provenance references, not data-provider integrations.
    Instrument lookup and dataset-level consistency are deferred to later phases.
    """

    instrument_id: Identifier
    source_id: Identifier
    dataset_id: Identifier
    available_at: UtcTimestamp


class MarketBar(_Observation):
    """Complete OHLC bar over [start_time, end_time), with explicit price basis.

    available_at is the earliest time the complete bar can be used, including any
    publication delay. Explicit endpoints remain authoritative for calendar bars;
    Timeframe.D1 does not enforce a 24-hour duration. Missing volume is None, not 0.
    Initial contracts represent positive-priced Forex, equity, and crypto data.
    """

    timeframe: Timeframe
    price_type: PriceType
    start_time: UtcTimestamp
    end_time: UtcTimestamp
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal | None = None
    volume_type: VolumeType | None = None

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.available_at < self.end_time:
            raise ValueError("available_at must not precede end_time")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("OHLC must satisfy low <= open, close <= high")
        if (self.volume is None) != (self.volume_type is None):
            raise ValueError("volume and volume_type must be supplied together")
        if (
            self.volume is not None
            and self.volume_type is VolumeType.TICK_COUNT
            and self.volume != self.volume.to_integral_value()
        ):
            raise ValueError("tick-count volume must be a whole number")
        return self


class MarketQuote(_Observation):
    """Historical bid/ask snapshot; spread is not inferred or calculated here."""

    timestamp: UtcTimestamp
    bid: PositiveDecimal
    ask: PositiveDecimal

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if self.ask < self.bid:
            raise ValueError("ask must be at least bid")
        if self.available_at < self.timestamp:
            raise ValueError("available_at must not precede timestamp")
        return self
