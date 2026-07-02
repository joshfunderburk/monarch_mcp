"""Budget and cashflow MCP tools."""

from __future__ import annotations

from typing import Any

from monarch.client import get_client
from monarch.errors import monarch_tool
from monarch.server import DESTRUCTIVE, READ_ONLY, mcp

# ---------------------------------------------------------------------------
# Budgets and cashflow (read)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_budgets(
    start_date: str | None = None,
    end_date: str | None = None,
    use_legacy_goals: bool = False,
    use_v2_goals: bool = True,
) -> dict[str, Any]:
    """Get budgets for a date range."""
    return await get_client().get_budgets(
        start_date=start_date,
        end_date=end_date,
        use_legacy_goals=use_legacy_goals,
        use_v2_goals=use_v2_goals,
    )


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_cashflow(
    start_date: str, end_date: str, limit: int = 100
) -> dict[str, Any]:
    """Get cashflow data by category and merchant.

    limit caps the number of category/merchant rows returned; raise it for
    date ranges with more distinct categories or merchants than the default.
    """
    return await get_client().get_cashflow(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_cashflow_summary(
    start_date: str, end_date: str, limit: int = 100
) -> dict[str, Any]:
    """Get income, expense, and savings totals for a date range.

    limit caps the number of underlying category/merchant rows aggregated;
    raise it for date ranges with many distinct categories or merchants.
    """
    return await get_client().get_cashflow_summary(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Budgets (write)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def set_budget_amount(
    amount: float,
    category_id: str | None = None,
    category_group_id: str | None = None,
    start_date: str | None = None,
    apply_to_future: bool = False,
) -> dict[str, Any]:
    """Set a budget amount for a category or category group."""
    return await get_client().set_budget_amount(
        amount=amount,
        category_id=category_id,
        category_group_id=category_group_id,
        start_date=start_date,
        apply_to_future=apply_to_future,
    )


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def reset_budget(
    start_date: str | None = None,
) -> dict[str, Any]:
    """Reset ALL planned budget amounts for a month back to defaults.

    This clears every category and category-group budget for the given
    month at once; it cannot target a single category. start_date defaults
    to the start of the current month.
    """
    return await get_client().reset_budget(start_date=start_date)


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def update_flexible_budget(
    amount: float,
    start_date: str | None = None,
    apply_to_future: bool = False,
) -> dict[str, Any]:
    """Update the flexible budget bucket amount."""
    return await get_client().update_flexible_budget(
        amount=amount,
        start_date=start_date,
        apply_to_future=apply_to_future,
    )
