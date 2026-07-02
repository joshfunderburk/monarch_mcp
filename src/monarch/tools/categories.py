"""Category and tag MCP tools."""

from __future__ import annotations

from typing import Any

from monarch.client import get_client
from monarch.errors import monarch_tool
from monarch.server import DESTRUCTIVE, READ_ONLY, mcp


def resolve_category_id(categories: list[dict[str, Any]], name: str) -> str:
    """Resolve a category name to its id (case-insensitive)."""
    needle = name.strip().casefold()
    matches = [c for c in categories if c.get("name", "").casefold() == needle]
    if not matches:
        available = sorted(c.get("name", "") for c in categories if c.get("name"))
        preview = ", ".join(available[:10])
        suffix = "..." if len(available) > 10 else ""
        raise ValueError(
            f"Category not found: {name!r}. Examples: {preview}{suffix}"
        )
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        raise ValueError(f"Ambiguous category {name!r}; multiple ids: {ids}")
    return matches[0]["id"]


# ---------------------------------------------------------------------------
# Categories (read)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_transaction_categories() -> dict[str, Any]:
    """Get all transaction categories."""
    return await get_client().get_transaction_categories()


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_transaction_category_groups() -> dict[str, Any]:
    """Get all transaction category groups."""
    return await get_client().get_transaction_category_groups()


# ---------------------------------------------------------------------------
# Categories (write)
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def create_transaction_category(
    group_id: str,
    transaction_category_name: str,
    icon: str = "❓",
) -> dict[str, Any]:
    """Create a new transaction category."""
    return await get_client().create_transaction_category(
        group_id=group_id,
        transaction_category_name=transaction_category_name,
        icon=icon,
    )


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def delete_transaction_category(category_id: str) -> bool:
    """Delete a transaction category."""
    return await get_client().delete_transaction_category(category_id)


# ---------------------------------------------------------------------------
# Tags (read)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_transaction_tags() -> dict[str, Any]:
    """Get all transaction tags."""
    return await get_client().get_transaction_tags()


# ---------------------------------------------------------------------------
# Tags (write)
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def create_transaction_tag(name: str, color: str) -> dict[str, Any]:
    """Create a new transaction tag."""
    return await get_client().create_transaction_tag(name=name, color=color)
