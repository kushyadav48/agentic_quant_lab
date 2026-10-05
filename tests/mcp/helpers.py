"""Shared MCP test helpers."""

TOOL_NAMES = [
    "validate_strategy_content", "validate_market_data", "resample_market_data",
    "compute_features", "evaluate_entry_risk", "analyze_performance",
    "run_backtest",
]


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


def valid_backtest_request():
    """Serialize an already-approved fixture; approval happens before MCP calls."""
    from tests.backtesting.helpers import CONFIG, INSTRUMENT, bars, strategy

    return {
        "strategy": strategy().model_dump(mode="json"),
        "bars": [bar.model_dump(mode="json") for bar in bars()],
        "instrument": INSTRUMENT.model_dump(mode="json"),
        "config": CONFIG.model_dump(mode="json"),
    }
