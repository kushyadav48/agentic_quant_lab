"""Phase 17 MCP boundary errors."""


class MCPAdapterError(Exception):
    """Base error for bounded MCP adapter failures."""


class MCPAdapterInputError(MCPAdapterError):
    """MCP-facing input could not be accepted safely."""


class MCPAdapterContractError(MCPAdapterError):
    """An internal MCP adapter contract was violated."""
