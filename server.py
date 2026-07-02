"""Monarch Money MCP server wrapping monarchmoneycommunity."""

from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable, Optional

from mcp.server.fastmcp import FastMCP
from monarchmoney import MonarchMoney, RequestFailedException
from monarchmoney.monarchmoney import BalanceHistoryRow

DEFAULT_SESSION_FILE = ".mm/mm_session.pickle"
SESSION_FILE = os.environ.get("MONARCH_SESSION_FILE", DEFAULT_SESSION_FILE)

mcp = FastMCP("monarch-money")

_client: Optional[MonarchMoney] = None


def get_client() -> MonarchMoney:
    """Return a lazily initialized MonarchMoney client with a loaded session."""
    global _client
    if _client is not None:
        return _client

    if not os.path.exists(SESSION_FILE):
        raise RuntimeError(
            f"No session file found at {SESSION_FILE}. "
            "Run `python login.py` to create one."
        )

    mm = MonarchMoney(session_file=SESSION_FILE)
    mm.load_session()
    _client = mm
    return _client


def monarch_tool[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap tool calls to surface Monarch API errors clearly."""

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except RequestFailedException as exc:
            raise RuntimeError(f"Monarch Money request failed: {exc}") from exc

    return wrapper


def _parse_balance_history_rows(
    rows: list[dict[str, Any]],
) -> list[BalanceHistoryRow]:
    parsed: list[BalanceHistoryRow] = []
    for row in rows:
        date_value = row["date"]
        if isinstance(date_value, str):
            date = datetime.fromisoformat(date_value)
        elif isinstance(date_value, datetime):
            date = date_value
        else:
            raise ValueError(
                "Each balance history row must include a date as an ISO string "
                "or datetime-compatible value."
            )

        parsed.append(
            BalanceHistoryRow(
                date=date,
                amount=float(row["amount"]),
                account_name=str(row["account_name"]),
            )
        )
    return parsed


# ---------------------------------------------------------------------------
# Accounts (read)
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def get_accounts() -> dict[str, Any]:
    """Get all Monarch Money accounts and household preferences."""
    return await get_client().get_accounts()


@mcp.tool()
@monarch_tool
async def get_account_type_options() -> dict[str, Any]:
    """Get available account types and subtypes."""
    return await get_client().get_account_type_options()


@mcp.tool()
@monarch_tool
async def get_recent_account_balances(lookback_days: int = 90) -> dict[str, Any]:
    """Get recent account balance snapshots for the last N days."""
    return await get_client().get_recent_account_balances(
        lookback_days=lookback_days
    )


@mcp.tool()
@monarch_tool
async def get_account_snapshots_by_type(
    start_date: str,
    timeframe: str,
) -> dict[str, Any]:
    """Get account balance snapshots grouped by account type."""
    return await get_client().get_account_snapshots_by_type(
        start_date=start_date,
        timeframe=timeframe,
    )


@mcp.tool()
@monarch_tool
async def get_aggregate_snapshots(
    start_date: str,
    timeframe: str,
) -> dict[str, Any]:
    """Get aggregate balance snapshots across all accounts."""
    return await get_client().get_aggregate_snapshots(
        start_date=start_date,
        timeframe=timeframe,
    )


# ---------------------------------------------------------------------------
# Transactions (read)
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def get_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    search: Optional[str] = None,
    category_ids: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    has_attachments: Optional[bool] = None,
    has_notes: Optional[bool] = None,
    needs_review: Optional[bool] = None,
    hidden_from_reports: Optional[bool] = None,
) -> dict[str, Any]:
    """Get transactions with optional filters and pagination."""
    return await get_client().get_transactions(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
        search=search,
        category_ids=category_ids,
        tag_ids=tag_ids,
        account_ids=account_ids,
        has_attachments=has_attachments,
        has_notes=has_notes,
        needs_review=needs_review,
        hidden_from_reports=hidden_from_reports,
    )


@mcp.tool()
@monarch_tool
async def get_transaction_details(
    transaction_id: str,
    redirect_posted: bool = True,
) -> dict[str, Any]:
    """Get detailed information for a single transaction."""
    return await get_client().get_transaction_details(
        transaction_id=transaction_id,
        redirect_posted=redirect_posted,
    )


@mcp.tool()
@monarch_tool
async def get_transaction_splits(transaction_id: str) -> dict[str, Any]:
    """Get split details for a transaction."""
    return await get_client().get_transaction_splits(transaction_id)


@mcp.tool()
@monarch_tool
async def find_duplicate_transactions(
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Find duplicate transactions within a date range."""
    return await get_client().find_duplicate_transactions(
        start_date=start_date,
        end_date=end_date,
    )


# ---------------------------------------------------------------------------
# Transactions (write)
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def create_transaction(
    date: str,
    account_id: str,
    amount: float,
    merchant_name: str,
    category_id: str,
    notes: str = "",
    update_balance: bool = False,
) -> dict[str, Any]:
    """Create a new transaction."""
    return await get_client().create_transaction(
        date=date,
        account_id=account_id,
        amount=amount,
        merchant_name=merchant_name,
        category_id=category_id,
        notes=notes,
        update_balance=update_balance,
    )


@mcp.tool()
@monarch_tool
async def update_transaction(
    transaction_id: str,
    category_id: Optional[str] = None,
    merchant_name: Optional[str] = None,
    goal_id: Optional[str] = None,
    amount: Optional[float] = None,
    date: Optional[str] = None,
    hide_from_reports: Optional[bool] = None,
    needs_review: Optional[bool] = None,
    reviewed: Optional[bool] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Update fields on an existing transaction."""
    return await get_client().update_transaction(
        transaction_id=transaction_id,
        category_id=category_id,
        merchant_name=merchant_name,
        goal_id=goal_id,
        amount=amount,
        date=date,
        hide_from_reports=hide_from_reports,
        needs_review=needs_review,
        reviewed=reviewed,
        notes=notes,
    )


@mcp.tool()
@monarch_tool
async def delete_transaction(transaction_id: str) -> bool:
    """Delete a transaction by ID."""
    return await get_client().delete_transaction(transaction_id)


@mcp.tool()
@monarch_tool
async def set_transaction_tags(
    transaction_id: str,
    tag_ids: list[str],
) -> dict[str, Any]:
    """Set tags on a transaction. Pass an empty list to clear all tags."""
    return await get_client().set_transaction_tags(transaction_id, tag_ids)


# ---------------------------------------------------------------------------
# Categories and tags
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def get_transaction_categories() -> dict[str, Any]:
    """Get all transaction categories."""
    return await get_client().get_transaction_categories()


@mcp.tool()
@monarch_tool
async def get_transaction_category_groups() -> dict[str, Any]:
    """Get all transaction category groups."""
    return await get_client().get_transaction_category_groups()


@mcp.tool()
@monarch_tool
async def create_transaction_category(
    group_id: str,
    transaction_category_name: str,
    icon: str = "",
) -> dict[str, Any]:
    """Create a new transaction category."""
    return await get_client().create_transaction_category(
        group_id=group_id,
        transaction_category_name=transaction_category_name,
        icon=icon,
    )


@mcp.tool()
@monarch_tool
async def delete_transaction_category(category_id: str) -> dict[str, Any]:
    """Delete a transaction category."""
    return await get_client().delete_transaction_category(category_id)


@mcp.tool()
@monarch_tool
async def get_transaction_tags() -> dict[str, Any]:
    """Get all transaction tags."""
    return await get_client().get_transaction_tags()


@mcp.tool()
@monarch_tool
async def create_transaction_tag(name: str, color: str) -> dict[str, Any]:
    """Create a new transaction tag."""
    return await get_client().create_transaction_tag(name=name, color=color)


# ---------------------------------------------------------------------------
# Budgets and cashflow
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def get_budgets(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
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


@mcp.tool()
@monarch_tool
async def get_cashflow(start_date: str, end_date: str) -> dict[str, Any]:
    """Get cashflow data by category and merchant."""
    return await get_client().get_cashflow(
        start_date=start_date,
        end_date=end_date,
    )


@mcp.tool()
@monarch_tool
async def set_budget_amount(
    amount: float,
    category_id: Optional[str] = None,
    category_group_id: Optional[str] = None,
    start_date: Optional[str] = None,
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


@mcp.tool()
@monarch_tool
async def reset_budget(
    category_id: Optional[str] = None,
    category_group_id: Optional[str] = None,
    start_date: Optional[str] = None,
) -> dict[str, Any]:
    """Reset a budget for a month back to defaults."""
    return await get_client().reset_budget(
        category_id=category_id,
        category_group_id=category_group_id,
        start_date=start_date,
    )


@mcp.tool()
@monarch_tool
async def update_flexible_budget(
    amount: float,
    start_date: Optional[str] = None,
    apply_to_future: bool = False,
) -> dict[str, Any]:
    """Update the flexible budget bucket amount."""
    return await get_client().update_flexible_budget(
        amount=amount,
        start_date=start_date,
        apply_to_future=apply_to_future,
    )


# ---------------------------------------------------------------------------
# Other reads
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def get_subscription_details() -> dict[str, Any]:
    """Get Monarch Money subscription details."""
    return await get_client().get_subscription_details()


@mcp.tool()
@monarch_tool
async def get_credit_history() -> dict[str, Any]:
    """Get credit score snapshots and related history."""
    return await get_client().get_credit_history()


# ---------------------------------------------------------------------------
# Full-surface writes
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def request_accounts_refresh_and_wait(
    account_ids: list[str],
) -> bool:
    """Refresh account data and wait until the refresh completes."""
    return await get_client().request_accounts_refresh_and_wait(account_ids)


@mcp.tool()
@monarch_tool
async def is_accounts_refresh_complete(account_ids: list[str]) -> bool:
    """Check whether account refresh jobs have completed."""
    return await get_client().is_accounts_refresh_complete(account_ids)


@mcp.tool()
@monarch_tool
async def create_manual_account(
    account_type: str,
    account_sub_type: str,
    is_in_net_worth: bool,
    account_name: str,
    account_balance: float = 0,
) -> dict[str, Any]:
    """Create a new manual account."""
    return await get_client().create_manual_account(
        account_type=account_type,
        account_sub_type=account_sub_type,
        is_in_net_worth=is_in_net_worth,
        account_name=account_name,
        account_balance=account_balance,
    )


@mcp.tool()
@monarch_tool
async def upload_account_balance_history(
    account_id: str,
    rows: list[dict[str, Any]],
    timeout: Optional[int] = None,
    delay: Optional[int] = None,
) -> bool:
    """Upload account balance history from rows with date, amount, and account_name."""
    csv_content = _parse_balance_history_rows(rows)
    kwargs: dict[str, Any] = {
        "account_id": account_id,
        "csv_content": csv_content,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if delay is not None:
        kwargs["delay"] = delay
    return await get_client().upload_account_balance_history(**kwargs)


if __name__ == "__main__":
    mcp.run()
