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
PAYDOWN_CHART_START = "2026-04"
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

  payload = build_report_payload(
    report_month=target_key,
    target=target,
    series=chart_series,
    debt_label=debt_label,
    accounts=accounts,
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
