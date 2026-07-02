"""Monarch Money MCP server package."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("monarch-money")

READ_ONLY = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

from monarch_mcp.tools import accounts, budgets, categories, transactions  # noqa: E402, F401


def run() -> None:
    mcp.run()
