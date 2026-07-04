"""Fetch Monarch Money data for offline report generation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from calendar import monthrange
from collections import defaultdict
from datetime import date
from typing import Any

from gql import gql

from monarch.client import get_client
from monarch.errors import slim
from monarch.tools.accounts import get_accounts

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
DATASETS = ("snapshots", "paydown", "accounts", "cashflow", "spending", "budget")
DEFAULT_DATA_DIR = os.path.join(_ROOT, "reports", "data")

# Accounts included in the monthly paydown report.
PAYDOWN_GROUPS: dict[str, set[tuple[str, str]]] = {
    "credit_card": {("credit", "credit_card")},
    "line_of_credit": {("loan", "line_of_credit")},
}
PAYDOWN_KEYS = set().union(*PAYDOWN_GROUPS.values())

# Expense categories that are debt payments rather than discretionary
# spending. Flagged in the spending dataset so the report can exclude them.
DEBT_PAYMENT_CATEGORIES = {
    "Auto Payment",
    "Mortgage",
    "Student Loans",
    "Loan Repayment",
}

# Merchant aggregates count both sides of transfers (e.g. loan payments), so
# the merchant leaderboard is fetched with an explicit category filter that
# keeps only non-debt expense/income categories.
_MERCHANT_SPENDING_QUERY = """
  query Web_GetCashFlowPage($filters: TransactionFilterInput) {
    byMerchant: aggregates(filters: $filters, groupBy: ["merchant"]) {
      groupBy {
        merchant {
          id
          name
          __typename
        }
        __typename
      }
      summary {
        sumIncome
        sumExpense
        __typename
      }
      __typename
    }
  }
"""


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


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _iter_months(start: date, end: date) -> list[str]:
    months: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _month_cutoff(month_key: str) -> str:
    year, month = (int(part) for part in month_key.split("-"))
    last_day = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def _next_month_end(value: str) -> str:
    """End of the month after the given date — budgets need one future month."""
    parsed = _parse_iso_date(value)
    year, month = parsed.year, parsed.month + 1
    if month > 12:
        year, month = year + 1, 1
    return _month_cutoff(f"{year:04d}-{month:02d}")


def _snapshot_balance(row: dict[str, Any]) -> float:
    if "signedBalance" in row:
        return float(row["signedBalance"])
    if "balance" in row:
        return float(row["balance"])
    raise KeyError("Snapshot row missing signedBalance/balance.")


def _paydown_group(account: dict[str, Any]) -> str | None:
    key = (account.get("type"), account.get("subtype"))
    for group, keys in PAYDOWN_GROUPS.items():
        if key in keys:
            return group
    return None


def _month_end_balances(
    history: list[dict[str, Any]],
    months: list[str],
) -> dict[str, float]:
    balances: dict[str, float] = {}
    if not history:
        return balances

    sorted_history = sorted(history, key=lambda row: row["date"][:10])
    index = 0
    latest: dict[str, Any] | None = None
    for month_key in months:
        cutoff = _month_cutoff(month_key)
        while index < len(sorted_history) and sorted_history[index]["date"][:10] <= cutoff:
            latest = sorted_history[index]
            index += 1
        if latest is not None:
            balances[month_key] = _snapshot_balance(latest)
    return balances


async def _load_account_month_end_balances(
    account: dict[str, Any],
    months: list[str],
    client: Any,
) -> tuple[dict[str, Any], str, dict[str, float]]:
    group = _paydown_group(account)
    if group is None:
        return account, "", {}
    history = await client.get_account_history(account_id=account["id"])
    return account, group, _month_end_balances(history, months)


async def _fetch_paydown_snapshots(
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    response = await get_accounts()
    accounts = [_slim_account(row) for row in response.get("accounts", [])]
    tracked = [account for account in accounts if _paydown_group(account) is not None]
    months = _iter_months(_parse_iso_date(start_date), _parse_iso_date(end_date))
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"credit_card": 0.0, "line_of_credit": 0.0},
    )

    client = get_client()
    results = await asyncio.gather(
        *(
            _load_account_month_end_balances(account, months, client)
            for account in tracked
        )
    )

    rows: list[dict[str, Any]] = []
    for account, group, month_balances in results:
        if not group:
            continue
        for month_key, balance in month_balances.items():
            totals[month_key][group] += balance
            rows.append(
                {
                    "month": month_key,
                    "kind": "account",
                    "account_id": account["id"],
                    "account_name": account["name"],
                    "group": group,
                    "balance": balance,
                }
            )

    for month_key in months:
        credit_card = totals[month_key]["credit_card"]
        line_of_credit = totals[month_key]["line_of_credit"]
        rows.extend(
            [
                {"month": month_key, "account_type": "credit_card", "balance": credit_card},
                {"month": month_key, "account_type": "line_of_credit", "balance": line_of_credit},
                {
                    "month": month_key,
                    "account_type": "paydown",
                    "balance": credit_card + line_of_credit,
                },
            ]
        )
    return rows


async def _non_debt_category_ids(client: Any) -> list[str]:
    """Category ids for the merchant leaderboard filter.

    Drops debt-payment categories and transfer-type categories: loan and
    credit card payments net to zero in category totals but would still
    show up on the expense side of merchant aggregates.
    """
    response = await client.get_transaction_categories()
    ids: list[str] = []
    for category in response.get("categories", []):
        if category.get("name") in DEBT_PAYMENT_CATEGORIES:
            continue
        if (category.get("group") or {}).get("type") == "transfer":
            continue
        ids.append(category["id"])
    return ids


async def _fetch_month_merchants(
    client: Any,
    *,
    start: str,
    end: str,
    category_ids: list[str],
) -> list[dict[str, Any]]:
    response = await client.gql_call(
        operation="Web_GetCashFlowPage",
        graphql_query=gql(_MERCHANT_SPENDING_QUERY),
        variables={
            "filters": {
                "search": "",
                "categories": category_ids,
                "accounts": [],
                "tags": [],
                "startDate": start,
                "endDate": end,
            },
        },
    )
    return response.get("byMerchant", [])


async def _fetch_month_spending(
    month_key: str,
    client: Any,
    *,
    merchant_category_ids: list[str],
) -> list[dict[str, Any]]:
    year, month = (int(part) for part in month_key.split("-"))
    start = f"{year:04d}-{month:02d}-01"
    end = _month_cutoff(month_key)
    response, merchant_items = await asyncio.gather(
        client.get_cashflow(start_date=start, end_date=end),
        _fetch_month_merchants(
            client,
            start=start,
            end=end,
            category_ids=merchant_category_ids,
        ),
    )

    rows: list[dict[str, Any]] = []
    summary_items = response.get("summary", [])
    if summary_items:
        summary = summary_items[0].get("summary", {})
        rows.append(
            {
                "month": month_key,
                "kind": "summary",
                "income": summary.get("sumIncome"),
                "expense": summary.get("sumExpense"),
                "savings": summary.get("savings"),
            }
        )

    for item in response.get("byCategory", []):
        category = (item.get("groupBy") or {}).get("category") or {}
        group = category.get("group") or {}
        amount = (item.get("summary") or {}).get("sum")
        if amount is None:
            continue
        row = {
            "month": month_key,
            "kind": "category",
            "name": category.get("name"),
            "group_type": group.get("type"),
            "amount": amount,
        }
        if category.get("name") in DEBT_PAYMENT_CATEGORIES:
            row["debt_payment"] = True
        rows.append(row)

    for item in merchant_items:
        merchant = (item.get("groupBy") or {}).get("merchant") or {}
        summary = item.get("summary") or {}
        expense = summary.get("sumExpense") or 0.0
        if not expense:
            continue
        rows.append(
            {
                "month": month_key,
                "kind": "merchant",
                "name": merchant.get("name"),
                "expense": expense,
            }
        )
    return rows


async def _fetch_spending(
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    months = _iter_months(_parse_iso_date(start_date), _parse_iso_date(end_date))
    client = get_client()
    merchant_category_ids = await _non_debt_category_ids(client)
    results = await asyncio.gather(
        *(
            _fetch_month_spending(
                month_key,
                client,
                merchant_category_ids=merchant_category_ids,
            )
            for month_key in months
        )
    )
    rows: list[dict[str, Any]] = []
    for month_rows in results:
        rows.extend(month_rows)
    return rows


def _budget_amount_row(month_amount: dict[str, Any]) -> dict[str, Any]:
    return {
        "month": (month_amount.get("month") or "")[:7],
        "planned": month_amount.get("plannedCashFlowAmount"),
        "actual": month_amount.get("actualAmount"),
        "remaining": month_amount.get("remainingAmount"),
        "rollover": month_amount.get("previousMonthRolloverAmount"),
    }


def _normalize_budget(response: dict[str, Any]) -> dict[str, Any]:
    budget_data = response.get("budgetData") or {}

    category_info: dict[str, dict[str, Any]] = {}
    for group in response.get("categoryGroups", []):
        for category in group.get("categories", []):
            category_info[category["id"]] = {
                "name": category.get("name"),
                "group": group.get("name"),
                "group_type": group.get("type"),
            }

    categories: list[dict[str, Any]] = []
    for item in budget_data.get("monthlyAmountsByCategory", []):
        category_id = (item.get("category") or {}).get("id")
        info = category_info.get(category_id, {})
        categories.append(
            {
                "id": category_id,
                "name": info.get("name"),
                "group": info.get("group"),
                "group_type": info.get("group_type"),
                "monthly": [
                    _budget_amount_row(row)
                    for row in item.get("monthlyAmounts", [])
                ],
            }
        )

    totals: list[dict[str, Any]] = []
    for row in budget_data.get("totalsByMonth", []):
        income = row.get("totalIncome") or {}
        expenses = row.get("totalExpenses") or {}
        totals.append(
            {
                "month": (row.get("month") or "")[:7],
                "income_planned": income.get("plannedAmount"),
                "income_actual": income.get("actualAmount"),
                "expenses_planned": expenses.get("plannedAmount"),
                "expenses_actual": expenses.get("actualAmount"),
                "expenses_remaining": expenses.get("remainingAmount"),
            }
        )

    return {"totals_by_month": totals, "categories": categories}


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
    if dataset == "paydown":
        return await _fetch_paydown_snapshots(
            start_date=start_date,
            end_date=end_date,
        )
    if dataset == "accounts":
        response = await get_accounts()
        return [_slim_account(row) for row in response.get("accounts", [])]
    if dataset == "cashflow":
        return await get_client().get_cashflow_summary(
            start_date=start_date,
            end_date=end_date,
        )
    if dataset == "spending":
        return await _fetch_spending(
            start_date=start_date,
            end_date=end_date,
        )
    if dataset == "budget":
        response = await get_client().get_budgets(
            start_date=start_date,
            end_date=_next_month_end(end_date),
        )
        return _normalize_budget(response)
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
