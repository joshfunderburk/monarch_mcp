"""Generate a Monarch Money PDF report from fetched JSON data."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import shutil
import socketserver
import sys
import threading
import time
import webbrowser
from calendar import month_name
from datetime import date
from pathlib import Path
from typing import Any, Callable

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
  sys.path.insert(0, _ROOT)

DEFAULT_DATA_PATH = os.path.join(_ROOT, "reports", "data", "paydown.json")
DEFAULT_SPENDING_PATH = os.path.join(_ROOT, "reports", "data", "spending.json")
DEFAULT_BUDGET_PATH = os.path.join(_ROOT, "reports", "data", "budget.json")
PAYDOWN_CHART_START = "2026-04"
SPENDING_CATEGORY_LIMIT = 10
SPENDING_MERCHANT_LIMIT = 10
DEFAULT_REPORT_DIR = os.path.join(_ROOT, "reports")
TEMPLATE_DIR = os.path.join(_ROOT, "reports", "template")
BUILD_DIR = os.path.join(_ROOT, "reports", "build")
DATA_PLACEHOLDER = "__REPORT_DATA__"
WATCH_RELOAD_SCRIPT = """<script>
setInterval(function () { location.reload(); }, 2000);
</script>"""
_PREVIEW_CONNECTION_ERRORS = (
  BrokenPipeError,
  ConnectionAbortedError,
  ConnectionResetError,
)


def _parse_month(value: str) -> tuple[int, int]:
  parts = value.split("-")
  if len(parts) != 2:
    raise SystemExit("--month must be YYYY-MM.")
  try:
    year = int(parts[0])
    month = int(parts[1])
  except ValueError as exc:
    raise SystemExit("--month must be YYYY-MM.") from exc
  if month < 1 or month > 12:
    raise SystemExit("--month must use a month between 01 and 12.")
  return year, month


def _month_key(year: int, month: int) -> str:
  return f"{year:04d}-{month:02d}"


def _month_label(month_key: str) -> str:
  year_str, month_str = month_key.split("-")
  return f"{month_name[int(month_str)]} {year_str}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
  index = year * 12 + (month - 1) + delta
  return index // 12, index % 12 + 1


def _format_currency(amount: float) -> str:
  return f"${amount:,.2f}"


def _load_snapshots(path: str) -> list[dict[str, Any]]:
  with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
  if not isinstance(payload, list):
    raise SystemExit(f"Expected a list in {path}, got {type(payload).__name__}.")
  return payload


def _parse_account_types(value: str) -> list[str]:
  types = [part.strip() for part in value.split(",") if part.strip()]
  if not types:
    raise SystemExit("--account-type must include at least one account type.")
  return types


def _balances_by_month(
  rows: list[dict[str, Any]],
  *,
  account_types: list[str],
) -> dict[str, float]:
  allowed = set(account_types)
  balances: dict[str, float] = {}
  for row in rows:
    account_type = row.get("account_type")
    if account_type not in allowed:
      continue
    month = row.get("month")
    balance = row.get("balance")
    if month is None or balance is None:
      continue
    month_key = str(month)
    balances[month_key] = balances.get(month_key, 0.0) + float(balance)
  return balances


def _debt_label(account_types: list[str]) -> str:
  if account_types == ["paydown"]:
    return "Credit card & line of credit"
  if account_types == ["credit_card"]:
    return "Credit card"
  if account_types == ["line_of_credit"]:
    return "Line of credit"
  if account_types == ["credit"]:
    return "Credit card debt"
  if account_types == ["loan"]:
    return "Loan debt"
  return "Total debt"


def _debt_amount(balance: float) -> float:
  """Convert a liability balance to a positive debt amount."""
  return abs(balance)


def compute_debt_paid_off_series(
  balances: dict[str, float],
  *,
  end_year: int,
  end_month: int,
  months: int = 12,
) -> list[dict[str, Any]]:
  series: list[dict[str, Any]] = []
  end_key = _month_key(end_year, end_month)

  for offset in range(months - 1, -1, -1):
    year, month = _shift_month(end_year, end_month, -offset)
    month_label = _month_key(year, month)
    prior_year, prior_month = _shift_month(year, month, -1)
    prior_label = _month_key(prior_year, prior_month)

    current_balance = balances.get(month_label)
    prior_balance = balances.get(prior_label)
    if current_balance is None or prior_balance is None:
      continue

    current_debt = _debt_amount(current_balance)
    prior_debt = _debt_amount(prior_balance)
    paid_off = prior_debt - current_debt

    series.append(
      {
        "month": month_label,
        "month_label": _month_label(month_label),
        "prior_debt": prior_debt,
        "current_debt": current_debt,
        "paid_off": paid_off,
        "is_target": month_label == end_key,
      }
    )
  return series


def _filter_series_from(
  series: list[dict[str, Any]],
  *,
  start_month: str,
) -> list[dict[str, Any]]:
  filtered = [point for point in series if point["month"] >= start_month]
  cumulative = 0.0
  for point in filtered:
    cumulative += point["paid_off"]
    point["cumulative_paid_off"] = cumulative
  return filtered


def _allowed_account_groups(account_types: list[str]) -> set[str]:
  if account_types == ["paydown"]:
    return {"credit_card", "line_of_credit"}
  return set(account_types)


def _account_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
  return [row for row in rows if row.get("kind") == "account"]


def _tracked_accounts_for_month(
  rows: list[dict[str, Any]],
  *,
  account_types: list[str],
  year: int,
  month: int,
) -> list[dict[str, Any]]:
  allowed_groups = _allowed_account_groups(account_types)
  target_key = _month_key(year, month)
  prior_year, prior_month = _shift_month(year, month, -1)
  prior_key = _month_key(prior_year, prior_month)

  by_account: dict[str, dict[str, Any]] = {}
  for row in _account_rows(rows):
    if row.get("group") not in allowed_groups:
      continue
    account_id = row.get("account_id")
    if account_id is None:
      continue
    entry = by_account.setdefault(
      account_id,
      {"name": row.get("account_name"), "balances": {}},
    )
    entry["balances"][row.get("month")] = float(row.get("balance", 0.0))

  accounts: list[dict[str, Any]] = []
  for account_id, entry in by_account.items():
    balances = entry["balances"]
    if target_key not in balances:
      continue
    current_debt = _debt_amount(balances[target_key])
    prior_debt = _debt_amount(balances[prior_key]) if prior_key in balances else current_debt
    change = current_debt - prior_debt
    if current_debt == 0 and change == 0:
      continue
    accounts.append(
      {
        "id": account_id,
        "name": entry["name"],
        "current_debt": current_debt,
        "change": change,
      }
    )
  accounts.sort(key=lambda account: account["current_debt"], reverse=True)
  return accounts


def _load_json_if_exists(path: str) -> Any | None:
  if not os.path.exists(path):
    return None
  with open(path, encoding="utf-8") as handle:
    return json.load(handle)


def _spending_rows_for(
  rows: list[dict[str, Any]],
  *,
  kind: str,
  month_key: str,
) -> list[dict[str, Any]]:
  return [row for row in rows if row.get("kind") == kind and row.get("month") == month_key]


def _is_controllable_expense(row: dict[str, Any]) -> bool:
  """Expense category rows minus debt payments (spending we can't control)."""
  return row.get("group_type") == "expense" and not row.get("debt_payment")


def _month_expense_totals(
  rows: list[dict[str, Any]],
  month_key: str,
) -> tuple[float, float]:
  """Return (controllable expense, debt payments) for a month."""
  expense = 0.0
  debt = 0.0
  for row in _spending_rows_for(rows, kind="category", month_key=month_key):
    if row.get("group_type") != "expense":
      continue
    amount = abs(float(row.get("amount") or 0.0))
    if row.get("debt_payment"):
      debt += amount
    else:
      expense += amount
  return expense, debt


def build_spending_section(
  rows: list[dict[str, Any]],
  *,
  year: int,
  month: int,
  history_months: int = 12,
) -> dict[str, Any] | None:
  target_key = _month_key(year, month)
  prior_year, prior_month = _shift_month(year, month, -1)
  prior_key = _month_key(prior_year, prior_month)

  summaries = {
    row["month"]: row
    for row in rows
    if row.get("kind") == "summary" and row.get("month")
  }
  if target_key not in summaries:
    return None

  monthly: list[dict[str, Any]] = []
  for offset in range(history_months - 1, -1, -1):
    point_year, point_month = _shift_month(year, month, -offset)
    point_key = _month_key(point_year, point_month)
    summary = summaries.get(point_key)
    if summary is None:
      continue
    expense, _ = _month_expense_totals(rows, point_key)
    monthly.append(
      {
        "month": point_key,
        "month_label": _month_label(point_key),
        "expense": expense,
        "income": float(summary.get("income") or 0.0),
        "is_target": point_key == target_key,
      }
    )

  prior_by_category = {
    row.get("name"): abs(float(row.get("amount") or 0.0))
    for row in _spending_rows_for(rows, kind="category", month_key=prior_key)
    if _is_controllable_expense(row)
  }
  category_rows = [
    row
    for row in _spending_rows_for(rows, kind="category", month_key=target_key)
    if _is_controllable_expense(row) and row.get("amount")
  ]
  category_rows.sort(key=lambda row: abs(float(row["amount"])), reverse=True)

  categories: list[dict[str, Any]] = []
  other_total = 0.0
  for index, row in enumerate(category_rows):
    amount = abs(float(row["amount"]))
    if index < SPENDING_CATEGORY_LIMIT:
      categories.append(
        {
          "name": row.get("name") or "Uncategorized",
          "amount": amount,
          "prior_amount": prior_by_category.get(row.get("name")),
        }
      )
    else:
      other_total += amount
  if other_total > 0:
    categories.append(
      {"name": "Everything else", "amount": other_total, "prior_amount": None}
    )

  merchant_rows = _spending_rows_for(rows, kind="merchant", month_key=target_key)
  merchant_rows.sort(key=lambda row: abs(float(row.get("expense") or 0.0)), reverse=True)
  merchants = [
    {
      "name": row.get("name") or "Unknown",
      "amount": abs(float(row.get("expense") or 0.0)),
    }
    for row in merchant_rows[:SPENDING_MERCHANT_LIMIT]
  ]

  target_summary = summaries[target_key]
  total_expense, debt_payment_total = _month_expense_totals(rows, target_key)
  prior_expense: float | None = None
  if prior_key in summaries:
    prior_expense, _ = _month_expense_totals(rows, prior_key)
  return {
    "total_expense": total_expense,
    "prior_expense": prior_expense,
    "debt_payment_total": debt_payment_total,
    "total_income": float(target_summary.get("income") or 0.0),
    "monthly": monthly,
    "categories": categories,
    "merchants": merchants,
  }


def _budget_totals_for(
  budget: dict[str, Any],
  month_key: str,
) -> dict[str, Any] | None:
  for row in budget.get("totals_by_month", []):
    if row.get("month") == month_key:
      return row
  return None


def _month_has_budget(totals: dict[str, Any] | None) -> bool:
  if totals is None:
    return False
  planned_income = float(totals.get("income_planned") or 0.0)
  planned_expenses = float(totals.get("expenses_planned") or 0.0)
  return planned_income > 0 or planned_expenses > 0


def build_budget_section(
  budget: dict[str, Any],
  *,
  year: int,
  month: int,
) -> dict[str, Any] | None:
  """Budget vs actual for the report month.

  If the report month has no planned amounts at all (budgets not yet set
  up), fall back to the next month that does and present it as a
  plan-only view without actuals.
  """
  target_key = _month_key(year, month)
  totals = _budget_totals_for(budget, target_key)
  budget_key = target_key
  plan_only = False

  if not _month_has_budget(totals):
    for offset in (1, 2):
      next_year, next_month = _shift_month(year, month, offset)
      next_key = _month_key(next_year, next_month)
      next_totals = _budget_totals_for(budget, next_key)
      if _month_has_budget(next_totals):
        totals = next_totals
        budget_key = next_key
        plan_only = True
        break

  if not _month_has_budget(totals):
    return None

  rows: list[dict[str, Any]] = []
  for category in budget.get("categories", []):
    if category.get("group_type") != "expense":
      continue
    monthly = next(
      (row for row in category.get("monthly", []) if row.get("month") == budget_key),
      None,
    )
    if monthly is None:
      continue
    planned = float(monthly.get("planned") or 0.0)
    actual = abs(float(monthly.get("actual") or 0.0))
    if plan_only:
      if planned == 0:
        continue
      rows.append(
        {
          "name": category.get("name") or "Uncategorized",
          "group": category.get("group"),
          "planned": planned,
        }
      )
      continue
    if planned == 0 and actual == 0:
      continue
    remaining = monthly.get("remaining")
    rows.append(
      {
        "name": category.get("name") or "Uncategorized",
        "group": category.get("group"),
        "planned": planned,
        "actual": actual,
        "remaining": float(remaining) if remaining is not None else planned - actual,
      }
    )
  if plan_only:
    rows.sort(key=lambda row: row["planned"], reverse=True)
  else:
    rows.sort(key=lambda row: (row["planned"], row["actual"]), reverse=True)

  return {
    "month": budget_key,
    "month_label": _month_label(budget_key),
    "plan_only": plan_only,
    "income_planned": float(totals.get("income_planned") or 0.0),
    "income_actual": float(totals.get("income_actual") or 0.0),
    "expenses_planned": float(totals.get("expenses_planned") or 0.0),
    "expenses_actual": abs(float(totals.get("expenses_actual") or 0.0)),
    "rows": rows,
  }


def _trailing_average(values: list[float], months: int = 3) -> float | None:
  recent = values[-months:]
  if not recent:
    return None
  return sum(recent) / len(recent)


def build_forecast_section(
  *,
  budget: dict[str, Any] | None,
  spending_rows: list[dict[str, Any]] | None,
  debt_paid_off_series: list[dict[str, Any]],
  year: int,
  month: int,
) -> dict[str, Any] | None:
  next_year, next_month = _shift_month(year, month, 1)
  next_key = _month_key(next_year, next_month)

  income_history: list[float] = []
  expense_history: list[float] = []
  if spending_rows:
    summaries = sorted(
      (row for row in spending_rows if row.get("kind") == "summary" and row.get("month")),
      key=lambda row: row["month"],
    )
    target_key = _month_key(year, month)
    for row in summaries:
      if row["month"] > target_key:
        continue
      income_history.append(float(row.get("income") or 0.0))
      expense_history.append(abs(float(row.get("expense") or 0.0)))

  income = None
  expenses = None
  income_basis = None
  expenses_basis = None

  next_totals = _budget_totals_for(budget, next_key) if budget else None
  if next_totals is not None:
    planned_income = float(next_totals.get("income_planned") or 0.0)
    planned_expenses = float(next_totals.get("expenses_planned") or 0.0)
    if planned_income > 0:
      income = planned_income
      income_basis = "budget"
    if planned_expenses > 0:
      expenses = planned_expenses
      expenses_basis = "budget"

  if income is None:
    income = _trailing_average(income_history)
    income_basis = "3-month average"
  if expenses is None:
    expenses = _trailing_average(expense_history)
    expenses_basis = "3-month average"

  if income is None or expenses is None:
    return None

  net_history = [
    inc - exp for inc, exp in zip(income_history, expense_history)
  ]
  recent_actual_net = _trailing_average(net_history)
  avg_paid_off = _trailing_average(
    [float(point["paid_off"]) for point in debt_paid_off_series]
  )

  return {
    "month": next_key,
    "month_label": _month_label(next_key),
    "projected_income": income,
    "income_basis": income_basis,
    "projected_expenses": expenses,
    "expenses_basis": expenses_basis,
    "available": income - expenses,
    "recent_actual_net": recent_actual_net,
    "recent_paid_off": avg_paid_off,
  }


def _default_out_path(year: int, month: int) -> str:
  return os.path.join(DEFAULT_REPORT_DIR, f"monarch_report_{year:04d}-{month:02d}.pdf")


def _build_dir_for_month(year: int, month: int) -> str:
  return os.path.join(BUILD_DIR, f"report_{year:04d}-{month:02d}")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Generate a Monarch Money PDF report from fetched JSON data.",
  )
  parser.add_argument(
    "--month",
    required=True,
    help="Report month in YYYY-MM format.",
  )
  parser.add_argument(
    "--data",
    default=DEFAULT_DATA_PATH,
    help="Snapshots JSON path. Default: reports/data/paydown.json",
  )
  parser.add_argument(
    "--spending-data",
    default=DEFAULT_SPENDING_PATH,
    help="Spending JSON path. Default: reports/data/spending.json (section skipped if missing).",
  )
  parser.add_argument(
    "--budget-data",
    default=DEFAULT_BUDGET_PATH,
    help="Budget JSON path. Default: reports/data/budget.json (section skipped if missing).",
  )
  parser.add_argument(
    "--out",
    help="Output PDF path. Default: reports/monarch_report_YYYY-MM.pdf",
  )
  parser.add_argument(
    "--account-type",
    default="paydown",
    help="Snapshot account_type to report on. Default: paydown.",
  )
  parser.add_argument(
    "--history-months",
    type=int,
    default=12,
    help="Number of months to show in the line chart. Default: 12.",
  )
  parser.add_argument(
    "--preview",
    action="store_true",
    help="Build the HTML report and open it in the default browser instead of writing a PDF.",
  )
  parser.add_argument(
    "--watch",
    action="store_true",
    help=(
      "With --preview: watch reports/template/ for CSS/HTML changes, rebuild, "
      "and auto-refresh the browser. Ctrl+C to stop."
    ),
  )
  return parser


def build_report_payload(
  *,
  report_month: str,
  target: dict[str, Any],
  series: list[dict[str, Any]],
  debt_label: str,
  accounts: list[dict[str, Any]] | None = None,
  spending: dict[str, Any] | None = None,
  budget: dict[str, Any] | None = None,
  forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
  month_year, month_num = report_month.split("-")
  paid_off = float(target["paid_off"])
  return {
    "month_label": f"{month_name[int(month_num)]} {month_year}",
    "generated_date": date.today().isoformat(),
    "debt_label": debt_label,
    "target": {
      "paid_off": paid_off,
      "prior_debt": float(target["prior_debt"]),
      "current_debt": float(target["current_debt"]),
    },
    "series": series,
    "accounts": accounts or [],
    "spending": spending,
    "budget": budget,
    "forecast": forecast,
  }


def _copy_template_assets(build_dir: str) -> None:
  os.makedirs(build_dir, exist_ok=True)
  shutil.copy2(
    os.path.join(TEMPLATE_DIR, "report.css"),
    os.path.join(build_dir, "report.css"),
  )
  vendor_src = os.path.join(TEMPLATE_DIR, "vendor")
  vendor_dst = os.path.join(build_dir, "vendor")
  if os.path.isdir(vendor_dst):
    shutil.rmtree(vendor_dst)
  shutil.copytree(vendor_src, vendor_dst)


def build_report_html(
  *,
  year: int,
  month: int,
  payload: dict[str, Any],
  watch: bool = False,
) -> str:
  build_dir = _build_dir_for_month(year, month)
  _copy_template_assets(build_dir)

  template_path = os.path.join(TEMPLATE_DIR, "report.html")
  with open(template_path, encoding="utf-8") as handle:
    template = handle.read()

  data_json = json.dumps(payload, indent=2)
  html = template.replace(DATA_PLACEHOLDER, data_json)
  if watch:
    html = html.replace("</body>", f"{WATCH_RELOAD_SCRIPT}\n</body>")

  html_path = os.path.join(build_dir, "report.html")
  with open(html_path, "w", encoding="utf-8") as handle:
    handle.write(html)

  return html_path


class _PreviewHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
  def handle(self) -> None:
    try:
      super().handle()
    except _PREVIEW_CONNECTION_ERRORS:
      pass

  def copyfile(self, source: Any, outputfile: Any) -> None:
    try:
      super().copyfile(source, outputfile)
    except _PREVIEW_CONNECTION_ERRORS:
      pass


class _PreviewThreadingServer(socketserver.ThreadingTCPServer):
  allow_reuse_address = True
  daemon_threads = True

  def handle_error(
    self,
    request: Any,
    client_address: tuple[str, int],
  ) -> None:
    _, exc, _ = sys.exc_info()
    if isinstance(exc, _PREVIEW_CONNECTION_ERRORS):
      return
    super().handle_error(request, client_address)


def _start_preview_server(build_dir: str, port: int = 8765) -> tuple[socketserver.BaseServer, str]:
  handler = functools.partial(_PreviewHTTPRequestHandler, directory=build_dir)

  for attempt in range(10):
    try:
      httpd = _PreviewThreadingServer(("127.0.0.1", port), handler)
      break
    except OSError:
      port += 1
  else:
    raise SystemExit("Could not find an open port for the preview server.")

  thread = threading.Thread(target=httpd.serve_forever, daemon=True)
  thread.start()
  return httpd, f"http://127.0.0.1:{port}/report.html"


def _preview_report(html_path: str) -> None:
  uri = Path(html_path).resolve().as_uri()
  print(f"Preview: {html_path}")
  webbrowser.open(uri)


def _write_preview_shortcut(build_dir: str, preview_url: str) -> str:
  shortcut_path = os.path.join(build_dir, "open-preview.url")
  with open(shortcut_path, "w", encoding="utf-8") as handle:
    handle.write("[InternetShortcut]\n")
    handle.write(f"URL={preview_url}\n")
  return shortcut_path


def _open_preview_url(preview_url: str) -> None:
  if not preview_url.startswith("http://") and not preview_url.startswith("https://"):
    raise SystemExit(f"Invalid preview URL: {preview_url}")
  webbrowser.open(preview_url)


def _template_mtimes() -> dict[str, float]:
  mtimes: dict[str, float] = {}
  root = Path(TEMPLATE_DIR)
  for path in root.rglob("*"):
    if path.is_file() and path.suffix in {".css", ".html"}:
      mtimes[str(path)] = path.stat().st_mtime
  return mtimes


def _watch_template(rebuild: Callable[[], str]) -> None:
  print("Watching reports/template/ — edit report.css or report.html, then save.")
  print("Preview auto-refreshes every 2s. Harmless connection resets are suppressed. Ctrl+C to stop.")
  seen = _template_mtimes()
  while True:
    time.sleep(0.4)
    current = _template_mtimes()
    if current == seen:
      continue
    seen = current
    html_path = rebuild()
    print(f"Rebuilt -> {html_path}")


def _render_pdf(html_path: str, out_path: str) -> None:
  try:
    from playwright.sync_api import sync_playwright
  except ImportError as exc:
    raise SystemExit(
      "Playwright is required for PDF export. Install with: pip install playwright && playwright install chromium"
    ) from exc

  uri = Path(html_path).resolve().as_uri()
  os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

  with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    try:
      page = browser.new_page()
      page.goto(uri, wait_until="networkidle")
      page.wait_for_function("window.reportReady === true", timeout=15000)
      page.pdf(
        path=out_path,
        format="Letter",
        print_background=True,
        margin={"top": "0.6in", "right": "0.6in", "bottom": "0.6in", "left": "0.6in"},
      )
    finally:
      browser.close()


def main() -> None:
  args = build_parser().parse_args()
  if args.history_months < 2:
    raise SystemExit("--history-months must be at least 2.")

  year, month = _parse_month(args.month)
  account_types = _parse_account_types(args.account_type)
  debt_label = _debt_label(account_types)
  out_path = args.out or _default_out_path(year, month)
  rows = _load_snapshots(args.data)
  balances = _balances_by_month(rows, account_types=account_types)
  series = compute_debt_paid_off_series(
    balances,
    end_year=year,
    end_month=month,
    months=args.history_months,
  )
  if not series:
    raise SystemExit(
      f"No month-over-month debt data found for {args.month} in {args.data}."
    )

  target_key = _month_key(year, month)
  target = next((point for point in series if point["month"] == target_key), None)
  if target is None:
    raise SystemExit(
      f"Missing balance data for {args.month} or the prior month in {args.data}."
    )

  chart_series = _filter_series_from(series, start_month=PAYDOWN_CHART_START)
  accounts = _tracked_accounts_for_month(
    rows,
    account_types=account_types,
    year=year,
    month=month,
  )

  spending_rows = _load_json_if_exists(args.spending_data)
  budget_data = _load_json_if_exists(args.budget_data)

  spending = None
  if spending_rows:
    spending = build_spending_section(
      spending_rows,
      year=year,
      month=month,
      history_months=args.history_months,
    )
    if spending is None:
      print(f"Warning: no spending summary for {args.month}; skipping spending section.")

  budget = None
  if budget_data:
    budget = build_budget_section(budget_data, year=year, month=month)
    if budget is None:
      print(f"Warning: no budget totals for {args.month}; skipping budget section.")

  forecast = build_forecast_section(
    budget=budget_data,
    spending_rows=spending_rows,
    debt_paid_off_series=chart_series,
    year=year,
    month=month,
  )

  payload = build_report_payload(
    report_month=target_key,
    target=target,
    series=chart_series,
    debt_label=debt_label,
    accounts=accounts,
    spending=spending,
    budget=budget,
    forecast=forecast,
  )

  def rebuild() -> str:
    return build_report_html(
      year=year,
      month=month,
      payload=payload,
      watch=args.watch,
    )

  html_path = rebuild()

  if args.watch:
    build_dir = _build_dir_for_month(year, month)
    httpd, preview_url = _start_preview_server(build_dir)
    shortcut_path = _write_preview_shortcut(build_dir, preview_url)
    print(f"Preview URL: {preview_url}")
    print(f"Shortcut:    {shortcut_path}  (double-click to open in your browser)")
    print()
    print("Cursor Browser: open the Browser tab, click the address bar, paste EXACTLY:")
    print(f"  {preview_url}")
    print("  (must start with http:// — do not use file:// or a workspace path)")
    print()
    _open_preview_url(preview_url)
    try:
      _watch_template(rebuild)
    except KeyboardInterrupt:
      print("\nStopped watch mode.")
    finally:
      httpd.shutdown()
    return

  if args.preview:
    _preview_report(html_path)
    return

  _render_pdf(html_path, out_path)

  paid_off = float(target["paid_off"])
  prior_debt = float(target["prior_debt"])
  current_debt = float(target["current_debt"])
  direction = "paid off" if paid_off >= 0 else "debt increased"
  print(
    f"{month_name[month]} {year}: {direction} {_format_currency(abs(paid_off))} "
    f"(balance {_format_currency(prior_debt)} -> {_format_currency(current_debt)}) | "
    f"PDF: {out_path}"
  )


if __name__ == "__main__":
  main()
