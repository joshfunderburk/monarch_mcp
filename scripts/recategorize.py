"""Recategorize transactions by search criteria."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from monarch_mcp.tools.categories import (  # noqa: E402
    get_transaction_categories,
    resolve_category_id,
)
from monarch_mcp.tools.transactions import (  # noqa: E402
    get_transactions,
    update_transaction,
)

PAGE_SIZE = 100
SAMPLE_SIZE = 5


async def fetch_matching_transactions(
    *,
    match: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    results: list[dict] = []
    offset = 0
    while True:
        page = await get_transactions(
            start_date=start_date,
            end_date=end_date,
            search=match,
            limit=PAGE_SIZE,
            offset=offset,
        )
        batch = page["results"]
        if not batch:
            break
        results.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return results


def filter_by_category(
    transactions: list[dict], category_id: str | None
) -> list[dict]:
    if category_id is None:
        return transactions
    return [tx for tx in transactions if tx.get("category_id") == category_id]


def format_sample(tx: dict) -> str:
    merchant = tx.get("merchant") or tx.get("description") or "?"
    tx_date = tx.get("date", "?")
    amount = tx.get("amount", "?")
    category = tx.get("category") or "?"
    return f"{tx_date} {merchant} {amount} ({category})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recategorize Monarch transactions by search criteria."
    )
    parser.add_argument(
        "--match",
        required=True,
        help="Search string passed to Monarch transaction search.",
    )
    parser.add_argument(
        "--to-category",
        required=True,
        help="Target category name.",
    )
    parser.add_argument(
        "--from-category",
        help="Only update transactions currently in this category.",
    )
    parser.add_argument(
        "--start",
        default="2000-01-01",
        help="Start date (YYYY-MM-DD). Default: 2000-01-01.",
    )
    parser.add_argument(
        "--end",
        default=date.today().isoformat(),
        help="End date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of transactions to update.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates. Without this flag, only a dry-run summary is printed.",
    )
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer.")

    categories_response = await get_transaction_categories()
    categories = categories_response.get("categories", [])
    try:
        to_category_id = resolve_category_id(categories, args.to_category)
        from_category_id = (
            resolve_category_id(categories, args.from_category)
            if args.from_category
            else None
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    matched = await fetch_matching_transactions(
        match=args.match,
        start_date=args.start,
        end_date=args.end,
    )
    in_scope = filter_by_category(matched, from_category_id)
    if args.limit is not None:
        in_scope = in_scope[: args.limit]

    print(f'Matched {len(matched)} transactions for "{args.match}"')
    if args.from_category:
        print(f"In-scope (from={args.from_category}): {len(in_scope)}")
    else:
        print(f"In-scope: {len(in_scope)}")

    if not in_scope:
        print("Nothing to update.")
        return

    mode = "apply" if args.apply else "dry-run"
    print(f"[{mode}] Would set category -> {args.to_category}")

    for tx in in_scope[:SAMPLE_SIZE]:
        print(f"Sample: {format_sample(tx)}")
    if len(in_scope) > SAMPLE_SIZE:
        print(f"... and {len(in_scope) - SAMPLE_SIZE} more")

    if not args.apply:
        print("Run with --apply to commit.")
        return

    updated = 0
    failed: list[tuple[str, str]] = []
    for tx in in_scope:
        tx_id = tx["id"]
        try:
            await update_transaction(
                transaction_id=tx_id,
                category_id=to_category_id,
            )
            updated += 1
            if updated % 10 == 0:
                print(f"  ... {updated}/{len(in_scope)}")
        except Exception as exc:  # noqa: BLE001
            failed.append((tx_id, str(exc)))

    print(f"Updated {updated} transactions")
    if failed:
        print(f"Failed {len(failed)}:")
        for tx_id, err in failed[:5]:
            print(f"  {tx_id}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
