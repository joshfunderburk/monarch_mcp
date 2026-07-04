"""Build a single self-contained HTML report for manual editing."""

from __future__ import annotations

import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.generate_report import (  # noqa: E402
    _parse_month,
    build_report_html,
    build_report_payload,
    compute_debt_paid_off_series,
    _balances_by_month,
    _debt_label,
    _filter_series_from,
    _format_currency,
    _load_snapshots,
    _month_key,
    _parse_account_types,
    _tracked_accounts_for_month,
    PAYDOWN_CHART_START,
    DEFAULT_DATA_PATH,
)


def _format_currency_signed(amount: float) -> str:
    sign = "+" if amount > 0 else "-" if amount < 0 else ""
    return f"{sign}{_format_currency(abs(amount))}"


def _render_account_rows(accounts: list[dict]) -> str:
    rows: list[str] = []
    for account in accounts:
        change = float(account["change"])
        change_class = "positive" if change <= 0 else "negative"
        rows.append(
            "<tr>"
            f"<td>{account['name']}</td>"
            f"<td>{_format_currency(float(account['current_debt']))}</td>"
            f'<td class="{change_class}">{_format_currency_signed(change)}</td>'
            "</tr>"
        )
    return "\n            ".join(rows)


def _render_chart_fallback(series: list[dict]) -> str:
    if not series:
        return ""
    max_value = max(
        max(float(point["paid_off"]) for point in series),
        max(float(point["cumulative_paid_off"]) for point in series),
        1.0,
    )
    width = 360
    height = 220
    padding = 28
    plot_width = width - padding * 2
    plot_height = height - padding * 2
    bar_gap = plot_width / max(len(series), 1)
    bar_width = min(42, bar_gap * 0.55)

    bars: list[str] = []
    line_points: list[str] = []
    labels: list[str] = []
    for index, point in enumerate(series):
        x_center = padding + bar_gap * index + bar_gap / 2
        paid_off = float(point["paid_off"])
        cumulative = float(point["cumulative_paid_off"])
        bar_height = (paid_off / max_value) * plot_height
        bar_x = x_center - bar_width / 2
        bar_y = padding + plot_height - bar_height
        fill = "#e8a87c" if point.get("is_target") else "#d9c8dd"
        bars.append(
            f'<rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="6" fill="{fill}" />'
        )
        line_y = padding + plot_height - (cumulative / max_value) * plot_height
        line_points.append(f"{x_center:.1f},{line_y:.1f}")
        label = point.get("month_label", point.get("month", ""))
        labels.append(
            f'<text x="{x_center:.1f}" y="{height - 8}" text-anchor="middle" '
            f'font-size="10" fill="#6b5b6e">{label}</text>'
        )

    polyline = " ".join(line_points)
    return f"""
        <svg class="chart-fallback" viewBox="0 0 {width} {height}" role="img" aria-label="Paid off month over month preview">
          <line x1="{padding}" y1="{padding + plot_height}" x2="{width - padding}" y2="{padding + plot_height}" stroke="#e8dceb" />
          {''.join(bars)}
          <polyline points="{polyline}" fill="none" stroke="#6b4e71" stroke-width="2" />
          {''.join(labels)}
        </svg>"""


def _build_body_markup(payload: dict) -> str:
    paid_off = float(payload["target"]["paid_off"])
    accent_class = "positive" if paid_off >= 0 else "negative"
    accounts = payload.get("accounts", [])
    series = payload.get("series", [])

    return f"""<article class="page">
    <header class="page-header">
      <h1 class="page-title">Funderburk Finances</h1>
      <p class="page-meta" id="generated-date">Generated {payload["generated_date"]}</p>
    </header>

    <h2 class="section-title">Debt Pay Down</h2>

    <section class="debt-section" aria-label="Debt pay down">
      <div class="debt-left">
        <div class="summary-card">
          <p class="summary-heading">Debt Paid Off</p>
          <div class="amount {accent_class}" id="summary-amount">{_format_currency(abs(paid_off))}</div>
        </div>
        <table class="account-table" id="account-table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Balance</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody id="account-table-body">
            {_render_account_rows(accounts)}
          </tbody>
        </table>
      </div>

      <div class="debt-chart panel">
        <h3 class="chart-title" id="chart-title">Paid off month over month</h3>
        <div class="chart-wrap">
          {_render_chart_fallback(series)}
        </div>
      </div>
    </section>
  </article>"""


def build_editor_fragment(*, payload: dict, css: str) -> str:
    extra_css = """
.chart-fallback {
  width: 100%;
  height: 100%;
  display: block;
}
"""
    return f"<style>\n{css}\n{extra_css}\n</style>\n\n{_build_body_markup(payload)}\n"
def _prefill_static_content(html: str, payload: dict) -> str:
    paid_off = float(payload["target"]["paid_off"])
    accent_class = "positive" if paid_off >= 0 else "negative"
    accounts = payload.get("accounts", [])
    series = payload.get("series", [])

    html = html.replace(
        '<p class="page-meta" id="generated-date"></p>',
        f'<p class="page-meta" id="generated-date">Generated {payload["generated_date"]}</p>',
    )
    html = html.replace(
        '<h3 class="chart-title" id="chart-title"></h3>',
        '<h3 class="chart-title" id="chart-title">Paid off month over month</h3>',
    )
    html = html.replace(
        '<div class="amount" id="summary-amount"></div>',
        (
            f'<div class="amount {accent_class}" id="summary-amount">'
            f"{_format_currency(abs(paid_off))}</div>"
        ),
    )
    html = html.replace(
        '<tbody id="account-table-body"></tbody>',
        f'<tbody id="account-table-body">\n            {_render_account_rows(accounts)}\n          </tbody>',
    )
    html = html.replace(
        '<canvas id="paid-off-chart"></canvas>',
        f"{_render_chart_fallback(series)}\n          <canvas id=\"paid-off-chart\"></canvas>",
    )
    return html


def _load_report_css() -> str:
    css_path = os.path.join(_ROOT, "reports", "template", "report.css")
    with open(css_path, encoding="utf-8") as handle:
        return handle.read()


def build_standalone_html(*, year: int, month: int, payload: dict) -> str:
    html_path = build_report_html(year=year, month=month, payload=payload)
    build_dir = os.path.dirname(html_path)

    with open(html_path, encoding="utf-8") as handle:
        html = handle.read()
    with open(os.path.join(build_dir, "report.css"), encoding="utf-8") as handle:
        css = handle.read()
    with open(
        os.path.join(build_dir, "vendor", "chart.umd.js"),
        encoding="utf-8",
    ) as handle:
        chart_js = handle.read()

    html = html.replace(
        '<link rel="stylesheet" href="report.css">',
        f"<style>\n{css}\n.chart-fallback {{\n  width: 100%;\n  height: 100%;\n  display: block;\n}}\nbody.chart-ready .chart-fallback {{\n  display: none;\n}}\n</style>",
    )
    html = html.replace(
        '<script src="vendor/chart.umd.js"></script>',
        f"<script>\n{chart_js}\n</script>",
    )
    html = _prefill_static_content(html, payload)
    html = html.replace(
        "window.reportReady = true;",
        "window.reportReady = true;\n      document.body.classList.add('chart-ready');",
    )
    return html


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a single self-contained HTML report for manual editing.",
    )
    parser.add_argument("--month", required=True, help="Report month in YYYY-MM format.")
    parser.add_argument(
        "--data",
        default=DEFAULT_DATA_PATH,
        help="Snapshots JSON path. Default: reports/data/paydown.json",
    )
    parser.add_argument(
        "--account-type",
        default="paydown",
        help="Snapshot account_type to report on. Default: paydown.",
    )
    parser.add_argument(
        "--out",
        help="Output HTML path. Default: reports/report_YYYY-MM_standalone.html",
    )
    parser.add_argument(
        "--fragment",
        action="store_true",
        help="Output body-only HTML for rich text editors (no html/head/body tags).",
    )
    args = parser.parse_args()

    year, month = _parse_month(args.month)
    account_types = _parse_account_types(args.account_type)
    debt_label = _debt_label(account_types)
    rows = _load_snapshots(args.data)
    balances = _balances_by_month(rows, account_types=account_types)
    series = compute_debt_paid_off_series(
        balances,
        end_year=year,
        end_month=month,
        months=12,
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

    if args.fragment:
        html = build_editor_fragment(payload=payload, css=_load_report_css())
        default_name = f"report_{year:04d}-{month:02d}_fragment.html"
    else:
        html = build_standalone_html(year=year, month=month, payload=payload)
        default_name = f"report_{year:04d}-{month:02d}_standalone.html"

    out_path = args.out or os.path.join(_ROOT, "reports", default_name)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(html)

    print(out_path)


if __name__ == "__main__":
    main()
