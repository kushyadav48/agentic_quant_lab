"""Context-independent, finite and bounded operation serialization."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib

import pytest

from quantlab.mcp.canonical import canonical_json, digest_json


def test_canonical_values_and_key_order_ignore_decimal_context():
    value = {"z": (Decimal("-0.00"), Decimal("1234.5000")),
             "a": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    with localcontext() as ctx:
        ctx.prec = 2
        encoded = canonical_json(value)
    assert encoded == '{"a":"2026-01-01T00:00:00+00:00","z":["0","1234.5"]}'
    assert canonical_json(dict(reversed(list(value.items())))) == encoded
    assert digest_json(encoded) == hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"),
                                  Decimal("Infinity"), Decimal("1e10000000"), Decimal("1e-10000000")])
def test_unsafe_or_unbounded_scalars_rejected_before_expansion(value):
    with pytest.raises(ValueError):
        canonical_json({"value": value})


def test_stream_limit_cycle_and_no_executable_objects():
    with pytest.raises(ValueError):
        canonical_json(["a"] * 100, max_bytes=10)
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        canonical_json(cycle)
    with pytest.raises(TypeError):
        canonical_json(object())


def test_clock_and_duration_encoding_is_exact():
    value = (time(9, 30, 0, 12), timedelta(days=1, seconds=60, microseconds=12),
             timedelta(microseconds=-1), timedelta(0))
    with localcontext() as ctx:
        ctx.prec = 2
        assert canonical_json(value) == '["09:30:00.000012","PT86460.000012S","-PT0.000001S","PT0S"]'
