"""Bounded MCP adapters over Quant Lab public services.

MCP is an interface boundary only. It owns no quantitative authority,
strategy approval, account state, fills, P&L, or autonomous tool loop.
"""

from .server import build_mcp_server

__all__ = ["build_mcp_server"]
