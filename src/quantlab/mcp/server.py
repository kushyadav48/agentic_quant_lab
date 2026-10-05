"""Fresh, stateless MCP server construction and local stdio entry point."""

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
from .tools import (
    analyze_performance,
    compute_features,
    evaluate_entry_risk,
    resample_market_data,
    run_backtest,
    validate_market_data,
    validate_strategy_content,
)


SERVER_NAME = "agentic-quant-lab"
SERVER_VERSION = "0.1.0"


def build_mcp_server() -> MCPServer:
    """Build exactly seven bounded tools without starting any transport."""
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
        ],
    )


def main() -> None:
    """Run the local MCP server over stdio only."""
    server = build_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
