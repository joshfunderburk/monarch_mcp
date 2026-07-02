"""Monarch Money MCP server wrapping monarchmoneycommunity."""

from __future__ import annotations

import asyncio
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
try:
    API_TIMEOUT = int(os.environ.get("MONARCH_TIMEOUT", "30"))
except ValueError as exc:
    raise RuntimeError(
        f"MONARCH_TIMEOUT must be an integer number of seconds, got "
        f"{os.environ.get('MONARCH_TIMEOUT')!r}."
    ) from exc

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


def _slim(value: Any) -> Any:
    """Recursively strip GraphQL noise from API responses.

    Removes `__typename` keys and keys with None values, which carry no
    information for tool consumers but inflate every response.
    """
    if isinstance(value, dict):
        return {
            k: _slim(v)
            for k, v in value.items()
            if k != "__typename" and v is not None
        }
    if isinstance(value, list):
        return [_slim(item) for item in value]
    return value


def monarch_tool[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap tool calls to surface Monarch API errors clearly and slim responses."""

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return _slim(await fn(*args, **kwargs))
        except TransportServerError as exc:
            if exc.code in (401, 403):
                global _client
                _client = None
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


def _flatten_transaction(txn: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw transaction payload to the fields that matter."""
    merchant = txn.get("merchant") or {}
    category = txn.get("category") or {}
    account = txn.get("account") or {}
    tags = txn.get("tags") or []
    flat = {
        "id": txn.get("id"),
        "date": txn.get("date"),
        "amount": txn.get("amount"),
        "pending": txn.get("pending"),
        "merchant": merchant.get("name"),
        "merchant_id": merchant.get("id"),
        "category": category.get("name"),
        "category_id": category.get("id"),
        "account": account.get("displayName"),
        "account_id": account.get("id"),
        "notes": txn.get("notes"),
        "needs_review": txn.get("needsReview"),
        "review_status": txn.get("reviewStatus"),
        "is_recurring": txn.get("isRecurring"),
        "is_split": txn.get("isSplitTransaction"),
        "tags": [t.get("name") for t in tags],
        "tag_ids": [t.get("id") for t in tags],
        "attachment_count": len(txn.get("attachments") or []),
        "hide_from_reports": txn.get("hideFromReports"),
    }
    if not flat["tags"]:
        del flat["tags"], flat["tag_ids"]
    if not flat["attachment_count"]:
        del flat["attachment_count"]
    # Boolean flags are omitted when false; absent means false.
    for flag in (
        "pending",
        "needs_review",
        "is_recurring",
        "is_split",
        "hide_from_reports",
    ):
        if flat[flag] is False:
            del flat[flag]
    return {k: v for k, v in flat.items() if v is not None}


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
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
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
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_type: Optional[str] = None,
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
    verbose: bool = False,
) -> dict[str, Any]:
    """Get transactions with optional filters and pagination.

    Returns at most `limit` transactions (default 100); use `offset` to
    paginate. start_date and end_date must be provided together.
    transaction_visibility accepts "hidden_transactions_only" or
    "all_transactions"; when omitted, only non-hidden transactions are
    returned.

    By default each transaction is flattened to its key fields (id, date,
    amount, merchant, category, account, notes, review status, tags).
    Boolean flags (pending, needs_review, is_recurring, is_split,
    hide_from_reports) are omitted when false. Pass verbose=true for the
    full raw payload including attachment details and timestamps.
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
    response = await get_client().get_transactions(**kwargs)
    if verbose:
        return response
    all_txns = response.get("allTransactions", {})
    return {
        "totalCount": all_txns.get("totalCount"),
        "results": [
            _flatten_transaction(txn) for txn in all_txns.get("results", [])
        ],
    }


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
    """Update fields on an existing transaction.

    Note: amount=0 and date="" are silently ignored (treated as "no
    change") by the underlying API and will not clear or zero those fields.
    """
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


_BULK_UPDATE_FIELDS = {
    "category_id",
    "merchant_name",
    "goal_id",
    "amount",
    "date",
    "hide_from_reports",
    "needs_review",
    "reviewed",
    "notes",
}


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def bulk_update_transactions(
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Update many transactions in one call.

    Each row requires "transaction_id" plus any fields to change:
    category_id, merchant_name, goal_id, amount, date, hide_from_reports,
    needs_review, reviewed, notes, and/or tag_ids (a list that overwrites
    existing tags; empty list clears them). Rows may set different values.

    Runs updates concurrently (max 5 at a time). Per-row failures do not
    abort the batch; the response lists which rows failed and why.

    Note: amount=0 and date="" are silently ignored (treated as "no
    change") by the underlying API and will not clear or zero those fields.
    """
    if not updates:
        raise ValueError("`updates` must contain at least one row.")

    for i, row in enumerate(updates, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {i}: each update must be an object.")
        if not row.get("transaction_id"):
            raise ValueError(f"Row {i}: missing required field 'transaction_id'.")
        unknown = set(row) - _BULK_UPDATE_FIELDS - {"transaction_id", "tag_ids"}
        if unknown:
            raise ValueError(
                f"Row {i}: unknown field(s) {sorted(unknown)}. Allowed: "
                f"transaction_id, tag_ids, {', '.join(sorted(_BULK_UPDATE_FIELDS))}."
            )
        if len(row) == 1:
            raise ValueError(
                f"Row {i}: no fields to update besides transaction_id."
            )

    client = get_client()
    semaphore = asyncio.Semaphore(5)

    async def apply_row(row: dict[str, Any]) -> Optional[dict[str, Any]]:
        transaction_id = row["transaction_id"]
        fields = {k: v for k, v in row.items() if k in _BULK_UPDATE_FIELDS}
        tag_ids = row.get("tag_ids")
        async with semaphore:
            try:
                if fields:
                    await client.update_transaction(
                        transaction_id=transaction_id, **fields
                    )
                if tag_ids is not None:
                    await client.set_transaction_tags(transaction_id, tag_ids)
            except Exception as exc:
                return {"transaction_id": transaction_id, "error": str(exc)}
        return None

    results = await asyncio.gather(*(apply_row(row) for row in updates))
    failed = [r for r in results if r is not None]
    return {"updated": len(updates) - len(failed), "failed": failed}


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def delete_transaction(transaction_id: str) -> bool:
    """Delete a transaction by ID."""
    return await get_client().delete_transaction(transaction_id)


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def set_transaction_tags(
    transaction_id: str,
    tag_ids: list[str],
) -> dict[str, Any]:
    """Set tags on a transaction. Overwrites existing tags; pass an empty
    list to clear all tags."""
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


@mcp.tool(annotations=DESTRUCTIVE)
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


@mcp.tool(annotations=DESTRUCTIVE)
@monarch_tool
async def reset_budget(
    start_date: Optional[str] = None,
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
async def request_accounts_refresh(
    account_ids: Optional[list[str]] = None,
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
    account_ids: Optional[list[str]] = None,
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
