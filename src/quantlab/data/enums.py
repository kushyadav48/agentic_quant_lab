"""Provider-neutral classifications for the initial market-data contracts."""

from enum import StrEnum


class AssetClass(StrEnum):
    """Forex is validated concretely; equity and crypto are extensibility labels."""

    FOREX = "forex"
    EQUITY = "equity"
    CRYPTO = "crypto"


class PriceType(StrEnum):
    """The price basis of an OHLC bar; never assumed to be mid-price."""

    BID = "bid"
    ASK = "ask"
    MID = "mid"
    TRADE = "trade"


class Timeframe(StrEnum):
    """Initial bar labels, not fixed-duration or trading-calendar calculations."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class VolumeType(StrEnum):
    """Explicit units distinguish traded quantity from Forex tick counts."""

    BASE = "base"
    QUOTE = "quote"
    TICK_COUNT = "tick_count"
