"""Bounded deterministic JSON serialization for operation identity and retention."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json


def canonical_json(value, *, max_bytes: int = 4_194_304) -> str:
    """Stream finite sorted JSON, without repr or ambient Decimal rounding.

    JSON's own tuple/list/dict traversal avoids building a second expanded
    object graph. Stop at the byte limit while encoding; individual Decimal
    expansions are capped before formatting, including sparse exponent inputs.
    """
    def convert(item):
        if isinstance(item, Decimal):
            _, digits, exponent = item.as_tuple()
            if not item.is_finite() or max(len(digits) + exponent, -exponent) > 4096:
                raise ValueError("Decimal expansion exceeds serialization bound")
            if item == 0:
                return "0"
            text = format(item, "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        if isinstance(item, (datetime, time)):
            return item.isoformat()
        if isinstance(item, timedelta):
            # Lossless ISO 8601 seconds, including microseconds; no float or
            # Decimal arithmetic and no change to the analytics Duration type.
            total = (item.days * 86400 + item.seconds) * 1_000_000 + item.microseconds
            seconds, micros = divmod(abs(total), 1_000_000)
            fraction = f".{micros:06d}".rstrip("0") if micros else ""
            sign = "-" if total < 0 else ""
            return f"{sign}PT{seconds}{fraction}S"
        if isinstance(item, Enum):
            return item.value
        raise TypeError("Unsupported canonical JSON value")
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False, default=convert)
    chunks = []
    size = 0
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > max_bytes:
            raise ValueError("Canonical JSON exceeds retention bound")
        chunks.append(chunk)
    return "".join(chunks)


def digest_json(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
