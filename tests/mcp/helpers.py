"""Shared MCP test helpers."""

ORIGINAL_TOOL_NAMES = [
    "validate_strategy_content", "validate_market_data", "resample_market_data",
    "compute_features", "evaluate_entry_risk", "analyze_performance",
    "run_backtest",
]

TOOL_NAMES = ORIGINAL_TOOL_NAMES + [
    "run_holdout", "run_walk_forward", "run_parameter_robustness", "build_ml_dataset",
    "train_ml_model", "predict_ml_oos", "ml_predictions_to_features",
    "submit_research_operation", "execute_research_operation", "get_research_operation",
    "cancel_research_operation",
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
