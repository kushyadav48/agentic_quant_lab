"""MCP server construction and local stdio entry point."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .models import StrategyValidationResult
from .tools import validate_strategy_content


SERVER_NAME = "agentic-quant-lab"
SERVER_VERSION = "0.1.0"


def build_mcp_server() -> MCPServer:
    """Build a fresh MCP server without starting any transport."""

    server = MCPServer(
        name=SERVER_NAME,
        title="Agentic Quant Research & Trading Lab",
        description=(
            "Bounded MCP interface to deterministic Quant Lab research services."
        ),
        version=SERVER_VERSION,
    )

    @server.tool(
        name="validate_strategy_content",
        description=(
            "Validate raw strategy content against the existing deterministic "
            "Quant Lab StrategyContent contract. This does not approve or execute "
            "a strategy."
        ),
        structured_output=True,
    )
    def validate_strategy_content_tool(
        content: Annotated[dict[str, Any], Field(description=(
            "Raw StrategyContent JSON object, not a StrategySpecification. "
            "Supply enum strings, arrays, and decimal strings as defined by the "
            "existing strategy contract; invalid content returns validation issues."
        ))],
    ) -> StrategyValidationResult:
        return validate_strategy_content(content)

    return server


def main() -> None:
    """Run the local MCP server over stdio only."""

    server = build_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
