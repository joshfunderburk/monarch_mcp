"""Transaction-related MCP tools."""

from __future__ import annotations

import asyncio
from typing import Any

from monarch.client import get_client
from monarch.errors import monarch_tool
from monarch.server import DESTRUCTIVE, READ_ONLY, mcp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Transactions (read)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
@monarch_tool
async def get_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    search: str | None = None,
    category_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    account_ids: list[str] | None = None,
    has_attachments: bool | None = None,
    has_notes: bool | None = None,
    needs_review: bool | None = None,
    hidden_from_reports: bool | None = None,
    is_split: bool | None = None,
    is_recurring: bool | None = None,
    imported_from_mint: bool | None = None,
    synced_from_institution: bool | None = None,
    transaction_visibility: str | None = None,
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
    start_date: str | None = None,
    end_date: str | None = None,
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
    category_id: str | None = None,
    merchant_name: str | None = None,
    goal_id: str | None = None,
    amount: float | None = None,
    date: str | None = None,
    hide_from_reports: bool | None = None,
    needs_review: bool | None = None,
    reviewed: bool | None = None,
    notes: str | None = None,
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

    async def apply_row(row: dict[str, Any]) -> dict[str, Any] | None:
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
