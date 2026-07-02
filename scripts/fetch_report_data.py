"""Fetch Monarch Money data for offline report generation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date
from typing import Any

from monarch.client import get_client
from monarch.errors import slim
from monarch.tools.accounts import get_accounts

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
DATASETS = ("snapshots", "accounts", "cashflow")
DEFAULT_DATA_DIR = os.path.join(_ROOT, "reports", "data")


def _default_start() -> str:
    """Thirteen months back — enough for a 12-point month-over-month chart."""
    today = date.today()
    month_index = today.year * 12 + today.month - 13
    year, month = divmod(month_index - 1, 12)
    return date(year, month + 1, 1).isoformat()


def _default_out(dataset: str) -> str:
    return os.path.join(DEFAULT_DATA_DIR, f"{dataset}.json")


def _slim_account(row: dict[str, Any]) -> dict[str, Any]:
    account_type = row.get("type") or {}
    subtype = row.get("subtype") or {}
    return {
        "id": row.get("id"),
        "name": row.get("displayName") or row.get("name"),
        "type": account_type.get("name") if isinstance(account_type, dict) else account_type,
        "subtype": subtype.get("name") if isinstance(subtype, dict) else subtype,
        "balance": row.get("currentBalance"),
    }


def _normalize_snapshots(
    response: dict[str, Any],
    *,
    account_types: set[str] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in response.get("snapshotsByAccountType", []):
        account_type = item.get("accountType")
        if account_types and account_type not in account_types:
            continue
        rows.append(
            {
                "month": item.get("month"),
                "account_type": account_type,
                "balance": item.get("balance"),
            }
        )
    rows.sort(key=lambda row: (row.get("month") or "", row.get("account_type") or ""))
    return rows


async def fetch_dataset(
    dataset: str,
    *,
    start_date: str,
    end_date: str,
    account_types: set[str] | None,
) -> Any:
    if dataset == "snapshots":
        response = await get_client().get_account_snapshots_by_type(
            start_date=start_date,
            timeframe="month",
        )
        return _normalize_snapshots(response, account_types=account_types)
    if dataset == "accounts":
        response = await get_accounts()
        return [_slim_account(row) for row in response.get("accounts", [])]
    if dataset == "cashflow":
        return await get_client().get_cashflow_summary(
            start_date=start_date,
            end_date=end_date,
        )
    raise ValueError(f"Unsupported dataset: {dataset}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Monarch Money data for offline report generation.",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=DATASETS,
        help="Dataset to fetch.",
    )
    parser.add_argument(
        "--start",
        default=_default_start(),
        help="Start date (YYYY-MM-DD). Default: 13 months ago.",
    )
    parser.add_argument(
        "--end",
        default=date.today().isoformat(),
        help="End date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--account-types",
        help="Comma-separated account types for snapshots (e.g. credit,loan).",
    )
    parser.add_argument(
        "--out",
        help="Output JSON path. Default: reports/data/<dataset>.json",
    )
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    out_path = args.out or _default_out(args.dataset)
    account_types = (
        {part.strip() for part in args.account_types.split(",") if part.strip()}
        if args.account_types
        else None
    )

    payload = await fetch_dataset(
        args.dataset,
        start_date=args.start,
        end_date=args.end,
        account_types=account_types,
    )
    payload = slim(payload)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")

    row_count = len(payload) if isinstance(payload, list) else 1
    print(f"{args.dataset}: {row_count} rows -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
