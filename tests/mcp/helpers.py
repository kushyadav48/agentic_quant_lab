"""Shared MCP test helpers."""


def valid_strategy_content():
    """Return fresh external JSON data, never canonical Python domain objects."""
    return {
        "name": "simple-long",
        "instruments": ["EURUSD"],
        "timeframe": "1h",
        "direction": "long",
        "long": {
            "entry": {
                "rules": [{
                    "left": {"kind": "market", "field": "close"},
                    "comparison": "gt",
                    "right": {"kind": "constant", "value": "1"},
                }],
            },
        },
        "provenance": {"origin": "manual"},
    }
