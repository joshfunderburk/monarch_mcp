"""Generate a Monarch Money PDF report from fetched JSON data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from calendar import month_name
from datetime import date
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
DEFAULT_DATA_PATH = os.path.join(_ROOT, "reports", "data", "snapshots.json")
DEFAULT_REPORT_DIR = os.path.join(_ROOT, "reports")


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


def _balances_by_month(
  rows: list[dict[str, Any]],
  *,
  account_type: str,
) -> dict[str, float]:
  balances: dict[str, float] = {}
  for row in rows:
    if row.get("account_type") != account_type:
      continue
    month = row.get("month")
    balance = row.get("balance")
    if month is None or balance is None:
      continue
    balances[str(month)] = float(balance)
  return balances


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
        "prior_debt": prior_debt,
        "current_debt": current_debt,
        "paid_off": paid_off,
        "is_target": month_label == end_key,
      }
    )
  return series


def _default_out_path(year: int, month: int) -> str:
  return os.path.join(DEFAULT_REPORT_DIR, f"monarch_report_{year:04d}-{month:02d}.pdf")


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
    help="Snapshots JSON path. Default: reports/data/snapshots.json",
  )
  parser.add_argument(
    "--out",
    help="Output PDF path. Default: reports/monarch_report_YYYY-MM.pdf",
  )
  parser.add_argument(
    "--account-type",
    default="credit",
    help="Account type for debt calculations. Default: credit.",
  )
  parser.add_argument(
    "--history-months",
    type=int,
    default=12,
    help="Number of months to show in the line chart. Default: 12.",
  )
  return parser


def _draw_report(
  *,
  out_path: str,
  report_month: str,
  target: dict[str, Any],
  series: list[dict[str, Any]],
) -> None:
  import matplotlib.pyplot as plt
  from matplotlib.backends.backend_pdf import PdfPages
  from matplotlib.ticker import FuncFormatter

  month_year, month_num = report_month.split("-")
  month_label = f"{month_name[int(month_num)]} {month_year}"
  paid_off = float(target["paid_off"])
  prior_debt = float(target["prior_debt"])
  current_debt = float(target["current_debt"])
  positive = paid_off >= 0
  headline = _format_currency(abs(paid_off))
  direction = "paid off" if positive else "debt increased"
  accent = "#1f7a4d" if positive else "#b42318"

  with PdfPages(out_path) as pdf:
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("#f7f7f8")

    fig.text(
      0.08,
      0.94,
      "Monarch Money Review",
      fontsize=22,
      fontweight="bold",
      color="#111827",
    )
    fig.text(0.08, 0.905, month_label, fontsize=14, color="#4b5563")
    fig.text(
      0.08,
      0.875,
      f"Generated {date.today().isoformat()}",
      fontsize=10,
      color="#6b7280",
    )

    card = fig.add_axes([0.08, 0.72, 0.84, 0.12])
    card.set_facecolor("white")
    card.set_xticks([])
    card.set_yticks([])
    for spine in card.spines.values():
      spine.set_visible(True)
      spine.set_color("#d1d5db")

    card.text(
      0.03,
      0.72,
      "Credit card debt paid off this month",
      fontsize=12,
      color="#374151",
      transform=card.transAxes,
    )
    card.text(
      0.03,
      0.28,
      headline,
      fontsize=28,
      fontweight="bold",
      color=accent,
      transform=card.transAxes,
    )
    card.text(
      0.03,
      0.05,
      f"{direction} · balance {_format_currency(prior_debt)} -> {_format_currency(current_debt)}",
      fontsize=10,
      color="#6b7280",
      transform=card.transAxes,
    )

    ax = fig.add_axes([0.1, 0.18, 0.82, 0.45])
    ax.set_facecolor("white")
    months = [point["month"] for point in series]
    values = [float(point["paid_off"]) for point in series]
    colors = [
      accent if point["is_target"] else "#2563eb" for point in series
    ]

    ax.axhline(0, color="#9ca3af", linewidth=1, linestyle="--")
    ax.bar(months, values, color=colors, width=0.65)
    ax.set_title(
      "Credit card debt paid off month over month",
      fontsize=13,
      color="#111827",
      pad=12,
    )
    ax.set_ylabel("Amount", color="#374151")
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _format_currency(value)))
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    for spine in ("top", "right"):
      ax.spines[spine].set_visible(False)

    fig.text(
      0.08,
      0.08,
      "Debt paid off = prior month-end credit card balance minus current month-end balance.",
      fontsize=9,
      color="#6b7280",
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
  args = build_parser().parse_args()
  if args.history_months < 2:
    raise SystemExit("--history-months must be at least 2.")

  year, month = _parse_month(args.month)
  out_path = args.out or _default_out_path(year, month)
  rows = _load_snapshots(args.data)
  balances = _balances_by_month(rows, account_type=args.account_type)
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

  os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
  _draw_report(
    out_path=out_path,
    report_month=target_key,
    target=target,
    series=series,
  )

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
