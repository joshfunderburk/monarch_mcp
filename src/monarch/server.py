"""FastMCP server instance, shared tool annotations, and entry point."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("monarch-money")

READ_ONLY = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def main() -> None:
    """Run the stdio MCP server."""
    from monarch import tools  # noqa: F401  # importing registers the tools

    mcp.run()
