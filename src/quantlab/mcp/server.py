"""Fresh MCP composition and local stdio entry point."""

from mcp.server.mcpserver import MCPServer

from .models import (
    BacktestExecutionRequest,
    EntryRiskRequest,
    FeatureComputationRequest,
    MarketDataResampleRequest,
    MarketDataValidationRequest,
    PerformanceAnalysisRequest,
)
from .registration import create_tool
from .operation_models import OperationReference, OperationSubmission
from .operation_tools import OperationTools
from .operations import ResearchOperations
from .research_models import (
    DatasetRequest, HoldoutRequest, PredictionFeaturesRequest, PredictionRequest,
    RobustnessRequest, TrainingRequest, WalkForwardRequest,
)
from .tools import (
    analyze_performance,
    compute_features,
    evaluate_entry_risk,
    resample_market_data,
    run_backtest,
    validate_market_data,
    validate_strategy_content,
    build_ml_dataset,
    ml_predictions_to_features,
    predict_ml_oos,
    run_holdout,
    run_parameter_robustness,
    run_walk_forward,
    train_ml_model,
)


SERVER_NAME = "agentic-quant-lab"
SERVER_VERSION = "0.1.0"


def build_mcp_server() -> MCPServer:
    """Build the explicit eighteen-tool surface and one isolated operation namespace."""
    operations = OperationTools(ResearchOperations())
    return MCPServer(
        name=SERVER_NAME,
        title="Agentic Quant Research & Trading Lab",
        description="Bounded MCP interface to deterministic Quant Lab research services.",
        version=SERVER_VERSION,
        tools=[
            create_tool(validate_strategy_content),
            create_tool(validate_market_data, request_contract=MarketDataValidationRequest),
            create_tool(resample_market_data, request_contract=MarketDataResampleRequest),
            create_tool(compute_features, request_contract=FeatureComputationRequest),
            create_tool(evaluate_entry_risk, request_contract=EntryRiskRequest),
            create_tool(analyze_performance, request_contract=PerformanceAnalysisRequest),
            create_tool(run_backtest, request_contract=BacktestExecutionRequest),
            create_tool(run_holdout, request_contract=HoldoutRequest),
            create_tool(run_walk_forward, request_contract=WalkForwardRequest),
            create_tool(run_parameter_robustness, request_contract=RobustnessRequest),
            create_tool(build_ml_dataset, request_contract=DatasetRequest),
            create_tool(train_ml_model, request_contract=TrainingRequest),
            create_tool(predict_ml_oos, request_contract=PredictionRequest),
            create_tool(ml_predictions_to_features, request_contract=PredictionFeaturesRequest),
            create_tool(operations.submit_research_operation, request_contract=OperationSubmission),
            create_tool(operations.execute_research_operation, request_contract=OperationReference),
            create_tool(operations.get_research_operation, request_contract=OperationReference),
            create_tool(operations.cancel_research_operation, request_contract=OperationReference),
        ],
    )


def main() -> None:
    """Run the local MCP server over stdio only."""
    server = build_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
