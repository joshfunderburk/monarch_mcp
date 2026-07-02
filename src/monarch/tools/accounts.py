"""Account-related MCP tools."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from monarchmoney.monarchmoney import BalanceHistoryRow

from monarch.client import get_client
from monarch.errors import monarch_tool
from monarch.server import READ_ONLY, mcp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
async def get_account_history(
    account_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Get historical daily balance snapshots for a single account.

    Without date bounds this returns the account's entire history, which
    can be very large for older accounts — pass start_date/end_date
    (YYYY-MM-DD, inclusive) to limit the range.
    """
    snapshots = await get_client().get_account_history(account_id=account_id)
    if start_date is not None:
        snapshots = [s for s in snapshots if s["date"] >= start_date]
    if end_date is not None:
        snapshots = [s for s in snapshots if s["date"] <= end_date]
    return snapshots


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
    start_date: str | None = None,
    end_date: str | None = None,
    account_type: str | None = None,
) -> dict[str, Any]:
    """Get daily aggregate net-value snapshots across all accounts.

    start_date defaults to 1 year ago (pass an explicit date for more);
    end_date defaults to today. account_type optionally restricts to a
    single account type.
    """
    if start_date is None:
        start_date = (date.today() - timedelta(days=365)).isoformat()
    return await get_client().get_aggregate_snapshots(
        start_date=start_date,
        end_date=end_date,
        account_type=account_type,
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
# Accounts (write)
# ---------------------------------------------------------------------------


@mcp.tool()
@monarch_tool
async def request_accounts_refresh(
    account_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Kick off an account data refresh and return immediately.

    account_ids=None refreshes all accounts. Does not wait for the sync to
    finish; poll is_accounts_refresh_complete to check on it. Preferred over
    request_accounts_refresh_and_wait for slow institutions, since it does
    not hold the tool call open.
    """
    client = get_client()
    if account_ids is None:
        account_data = await client.get_accounts()
        account_ids = [a["id"] for a in account_data["accounts"]]
    await client.request_accounts_refresh(account_ids)
    return {"success": True, "accounts_requested": len(account_ids)}


@mcp.tool()
@monarch_tool
async def request_accounts_refresh_and_wait(
    account_ids: list[str] | None = None,
    timeout: int = 120,
    delay: int = 5,
) -> dict[str, Any]:
    """Refresh account data and wait for completion.

    account_ids=None refreshes all accounts. Raises if the refresh does not
    complete within `timeout` seconds. This holds the tool call open while
    waiting; for slow institutions prefer request_accounts_refresh plus
    is_accounts_refresh_complete polling.
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
    account_ids: list[str] | None = None,
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
    timeout: int | None = None,
    delay: int | None = None,
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
