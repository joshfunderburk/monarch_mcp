"""Monarch Money MCP server wrapping monarchmoneycommunity."""

from __future__ import annotations

import os
import pickle
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from gql.transport.exceptions import TransportQueryError, TransportServerError
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from monarchmoney import LoginFailedException, MonarchMoney, RequestFailedException
from monarchmoney.monarchmoney import BalanceHistoryRow

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SESSION_FILE = os.path.join(_SCRIPT_DIR, ".mm", "mm_session.pickle")
SESSION_FILE = os.environ.get("MONARCH_SESSION_FILE", DEFAULT_SESSION_FILE)
API_TIMEOUT = int(os.environ.get("MONARCH_TIMEOUT", "30"))

mcp = FastMCP("monarch-money")

READ_ONLY = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

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

    mm = MonarchMoney(session_file=SESSION_FILE, timeout=API_TIMEOUT)
    try:
        mm.load_session()
    except (LoginFailedException, pickle.UnpicklingError, EOFError) as exc:
        raise RuntimeError(
            f"Session file {SESSION_FILE} is invalid or corrupt: {exc}. "
            "Re-run `python login.py` to create a new one."
        ) from exc
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
        except TransportServerError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(
                    "Monarch Money session is expired or invalid. "
                    "Re-run `python login.py` to create a new session."
                ) from exc
            raise RuntimeError(
                f"Monarch Money server error (HTTP {exc.code}): {exc}"
            ) from exc
        except TransportQueryError as exc:
            raise RuntimeError(
                f"Monarch Money API rejected the request: {exc}"
            ) from exc
        except RequestFailedException as exc:
            raise RuntimeError(f"Monarch Money request failed: {exc}") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError(
                f"Network error talking to Monarch Money: {exc!r}. "
                "If this is a timeout, raise MONARCH_TIMEOUT."
            ) from exc

    return wrapper


def _parse_row_date(date_value: Any, row_index: int) -> datetime:
    if isinstance(date_value, str):
        return datetime.fromisoformat(date_value)
    if isinstance(date_value, datetime):
        return date_value
    if isinstance(date_value, date):
        return datetime.combine(date_value, datetime.min.time())
    raise ValueError(
        f"Row {row_index}: date must be an ISO string, date, or datetime."
    )


def _parse_row_amount(row: dict[str, Any], row_index: int) -> float:
    amount_value = row.get("amount", row.get("balance"))
    if amount_value is None:
        raise ValueError(
            f"Row {row_index}: each row must include 'amount' or 'balance'."
        )
    try:
        return float(amount_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Row {row_index}: amount must be numeric, got {amount_value!r}."
        ) from exc


def _parse_balance_history_rows(
    rows: list[dict[str, Any]],
) -> list[BalanceHistoryRow]:
    parsed: list[BalanceHistoryRow] = []
    for i, row in enumerate(rows, start=1):
        if "date" not in row:
            raise ValueError(f"Row {i}: missing required field 'date'.")

        account_name = row.get("account_name")
        parsed.append(
            BalanceHistoryRow(
                date=_parse_row_date(row["date"], i),
                amount=_parse_row_amount(row, i),
                account_name=str(account_name) if account_name is not None else None,
            )
        )
    return parsed


def _validate_balance_history_rows(rows: list[BalanceHistoryRow]) -> None:
    if not rows:
        raise ValueError("At least one balance history row is required.")

    seen_dates: set[date] = set()
    today = date.today()
    for i, row in enumerate(rows, start=1):
        row_date = row.date.date()
        if row_date in seen_dates:
            raise ValueError(
                f"Row {i}: duplicate date {row_date.isoformat()}."
            )
        seen_dates.add(row_date)
        if row_date > today:
            raise ValueError(
                f"Row {i}: future date {row_date.isoformat()} is not supported."
            )


# ---------------------------------------------------------------------------
# Accounts (read)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_accounts() -> dict[str, Any]:
    """Get all Monarch Money accounts and household preferences."""
    return await get_client().get_accounts()


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_account_type_options() -> dict[str, Any]:
    """Get available account types and subtypes."""
    return await get_client().get_account_type_options()


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_recent_account_balances(lookback_days: int = 90) -> dict[str, Any]:
    """Get recent daily balance snapshots for all accounts over the last N days."""
    start_date = (date.today() - timedelta(days=lookback_days)).isoformat()
    return await get_client().get_recent_account_balances(start_date=start_date)


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_account_history(account_id: str) -> list[dict[str, Any]]:
    """Get historical daily balance snapshots for a single account."""
    return await get_client().get_account_history(account_id=account_id)


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_account_holdings(account_id: str) -> dict[str, Any]:
    """Get investment holdings for a brokerage-type account."""
    return await get_client().get_account_holdings(account_id)


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_institutions() -> dict[str, Any]:
    """Get linked institutions and their sync status."""
    return await get_client().get_institutions()


# ---------------------------------------------------------------------------
# Transactions (read)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
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
    is_split: Optional[bool] = None,
    is_recurring: Optional[bool] = None,
    imported_from_mint: Optional[bool] = None,
    synced_from_institution: Optional[bool] = None,
    transaction_visibility: Optional[str] = None,
) -> dict[str, Any]:
    """Get transactions with optional filters and pagination.

    Returns at most `limit` transactions (default 100); use `offset` to
    paginate. start_date and end_date must be provided together.
    transaction_visibility accepts "hidden_transactions_only" or
    "all_transactions"; when omitted, only non-hidden transactions are
    returned.
    """
    kwargs: dict[str, Any] = {}
    if start_date is not None:
        kwargs["start_date"] = start_date
    if end_date is not None:
        kwargs["end_date"] = end_date
    if limit is not None:
        kwargs["limit"] = limit
    if offset is not None:
        kwargs["offset"] = offset
    if search is not None:
        kwargs["search"] = search
    if category_ids is not None:
        kwargs["category_ids"] = category_ids
    if tag_ids is not None:
        kwargs["tag_ids"] = tag_ids
    if account_ids is not None:
        kwargs["account_ids"] = account_ids
    if has_attachments is not None:
        kwargs["has_attachments"] = has_attachments
    if has_notes is not None:
        kwargs["has_notes"] = has_notes
    if needs_review is not None:
        kwargs["needs_review"] = needs_review
    if hidden_from_reports is not None:
        kwargs["hidden_from_reports"] = hidden_from_reports
    if is_split is not None:
        kwargs["is_split"] = is_split
    if is_recurring is not None:
        kwargs["is_recurring"] = is_recurring
    if imported_from_mint is not None:
        kwargs["imported_from_mint"] = imported_from_mint
    if synced_from_institution is not None:
        kwargs["synced_from_institution"] = synced_from_institution
    if transaction_visibility is not None:
        kwargs["transaction_visibility"] = transaction_visibility
    return await get_client().get_transactions(**kwargs)


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_transaction_splits(transaction_id: str) -> dict[str, Any]:
    """Get split details for a transaction."""
    return await get_client().get_transaction_splits(transaction_id)


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_recurring_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Get upcoming recurring transaction items, including merchant and account."""
    return await get_client().get_recurring_transactions(
        start_date=start_date,
        end_date=end_date,
    )


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_transactions_summary() -> dict[str, Any]:
    """Get global transaction aggregates for the account."""
    return await get_client().get_transactions_summary()


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


@mcp.tool(annotations=DESTRUCTIVE)
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


@mcp.tool(annotations=DESTRUCTIVE)
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


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def delete_transaction_category(category_id: str) -> dict[str, Any]:
    """Delete a transaction category."""
    return await get_client().delete_transaction_category(category_id)


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_cashflow(start_date: str, end_date: str) -> dict[str, Any]:
    """Get cashflow data by category and merchant."""
    return await get_client().get_cashflow(
        start_date=start_date,
        end_date=end_date,
    )


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_cashflow_summary(start_date: str, end_date: str) -> dict[str, Any]:
    """Get income, expense, and savings totals for a date range."""
    return await get_client().get_cashflow_summary(
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


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_subscription_details() -> dict[str, Any]:
    """Get Monarch Money subscription details."""
    return await get_client().get_subscription_details()


@mcp.tool(annotations=READ_ONLY)
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
    account_ids: Optional[list[str]] = None,
    timeout: int = 300,
    delay: int = 10,
) -> dict[str, Any]:
    """Refresh account data and wait for completion.

    account_ids=None refreshes all accounts. Raises if the refresh does not
    complete within `timeout` seconds.
    """
    refreshed = await get_client().request_accounts_refresh_and_wait(
        account_ids=account_ids, timeout=timeout, delay=delay
    )
    if not refreshed:
        raise RuntimeError(
            f"Account refresh did not complete within {timeout} seconds. "
            "Use is_accounts_refresh_complete to poll for completion."
        )
    return {"success": True}


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def is_accounts_refresh_complete(
    account_ids: Optional[list[str]] = None,
) -> bool:
    """Check whether account refresh jobs have completed (all accounts if omitted)."""
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
) -> dict[str, Any]:
    """Upload account balance history from rows with date and amount (or balance).

    Each row needs a date (ISO string, date, or datetime) and amount or balance.
    account_name is optional when account_id is provided. For large histories,
    pass timeout=600 (or higher) to allow more time for Monarch to parse.
    """
    csv_content = _parse_balance_history_rows(rows)
    _validate_balance_history_rows(csv_content)

    kwargs: dict[str, Any] = {
        "account_id": account_id,
        "csv_content": csv_content,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if delay is not None:
        kwargs["delay"] = delay

    success = await get_client().upload_account_balance_history(**kwargs)
    if not success:
        raise RuntimeError(
            "Balance history upload did not complete. Common causes: parse timeout "
            "(try a higher timeout for large files), duplicate dates, future dates, "
            "invalid account_id, or wrong sign for liability accounts. "
            "Use get_account_history to verify whether snapshots were applied."
        )

    return {"success": True, "rows_uploaded": len(csv_content)}


if __name__ == "__main__":
    mcp.run()
