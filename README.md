# Monarch Money MCP Server

A local stdio MCP server that wraps the [`monarchmoneycommunity`](https://pypi.org/project/monarchmoneycommunity/) Python library, exposing Monarch Money accounts, transactions, budgets, and more as MCP tools for Cursor and other MCP clients.

## Project layout

```
├── src/monarch/        # the MCP server package
│   ├── server.py       # FastMCP instance, tool annotations, entry point
│   ├── client.py       # MonarchMoney client lifecycle and configuration
│   ├── errors.py       # error mapping and response slimming
│   ├── login.py        # interactive login (monarch-login)
│   └── tools/          # tool modules, one per domain
├── scripts/            # offline bulk-maintenance and report scripts
├── tests/              # unit tests for the pure data-shaping logic
└── reports/            # generated report data and PDFs (gitignored)
```

## Prerequisites

- Python 3.12+ (the server uses PEP 695 generic syntax)
- A Monarch Money account

## Setup

1. Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, activate with `.venv\Scripts\activate` instead.

2. Install the package in editable mode (add `[dev]` for lint/test tools):

```bash
pip install -e .
# or, for development:
pip install -e ".[dev]"
```

3. Create a session file (one-time, or when the token expires):

```bash
monarch-login
```

This saves your auth session to `.mm/mm_session.pickle` in the repo root. Re-running `monarch-login` always performs a fresh login, so use it any time API calls start failing with a "session is expired" error.

## Run the server

```bash
monarch-mcp
# equivalently:
python -m monarch
```

The server uses stdio transport by default.

## Cursor configuration

Add this to your Cursor MCP config (`mcp.json`), pointing at the venv where the package is installed:

```json
{
  "mcpServers": {
    "monarch-money": {
      "command": "/path/to/monarch_mcp/.venv/bin/python",
      "args": ["-m", "monarch"],
      "cwd": "/path/to/monarch_mcp"
    }
  }
}
```

The session file path defaults to `.mm/mm_session.pickle` in the repo root, so `MONARCH_SESSION_FILE` is optional. On Windows, point `command` at `.venv\\Scripts\\python.exe` and use Windows-style paths.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MONARCH_SESSION_FILE` | `<repo root>/.mm/mm_session.pickle` | Path to the saved session pickle |
| `MONARCH_TIMEOUT` | `30` | Timeout in seconds for each Monarch API call |

## Available tools

All responses are slimmed before being returned: GraphQL `__typename` keys and null fields are stripped to keep tool output small.

### Accounts (read)
- `get_accounts`
- `get_account_type_options`
- `get_recent_account_balances`
- `get_account_history`
- `get_account_snapshots_by_type`
- `get_aggregate_snapshots`
- `get_account_holdings`
- `get_institutions`

### Accounts (write)
- `request_accounts_refresh` — kicks off a sync and returns immediately (preferred for slow institutions)
- `request_accounts_refresh_and_wait` — blocks until the sync completes or times out
- `is_accounts_refresh_complete`
- `create_manual_account`
- `upload_account_balance_history`

### Transactions (read)
- `get_transactions` — flattened rows by default; pass `verbose: true` for the full raw payload
- `get_transaction_details`
- `get_transaction_splits`
- `find_duplicate_transactions`
- `get_recurring_transactions`
- `get_transactions_summary`

### Transactions (write)
- `create_transaction`
- `update_transaction`
- `bulk_update_transactions` — update many transactions in one call (different values per row, including tags)
- `delete_transaction`
- `set_transaction_tags`

### Categories and tags
- `get_transaction_categories`
- `get_transaction_category_groups`
- `create_transaction_category`
- `delete_transaction_category`
- `get_transaction_tags`
- `create_transaction_tag`

### Budgets and cashflow
- `get_budgets`
- `get_cashflow`
- `get_cashflow_summary`
- `set_budget_amount`
- `reset_budget`
- `update_flexible_budget`

## Scripts

Bulk maintenance and offline report tasks that are cheaper to run outside the MCP agent loop. The scripts import the installed `monarch` package, so run them from the venv where `pip install -e .` was done.

### Recategorize transactions

- `scripts/recategorize.py` — recategorize transactions by search criteria. Dry-run by default; pass `--apply` to commit.

  ```bash
  python scripts/recategorize.py --match "INTEREST CHARGE" --to-category "Financial Fees" --from-category "Interest"
  python scripts/recategorize.py --match "INTEREST CHARGE" --to-category "Financial Fees" --from-category "Interest" --apply
  ```

### PDF reports

Token-efficient pipeline: scripts fetch data to local JSON and generate a PDF. The agent should run these scripts rather than pulling balances through MCP.

- `scripts/fetch_report_data.py` — fetch Monarch data for offline reports.

  ```bash
  python scripts/fetch_report_data.py --dataset snapshots
  python scripts/fetch_report_data.py --dataset accounts
  python scripts/fetch_report_data.py --dataset cashflow --start 2026-06-01 --end 2026-06-30
  ```

  Datasets: `snapshots` (monthly balances by account type), `accounts` (current account list), `cashflow` (summary for a date range). Output defaults to `reports/data/<dataset>.json`.

- `scripts/generate_report.py` — build a PDF handout from fetched JSON (HTML/CSS template + Chart.js, exported via Playwright).

  ```bash
  python scripts/fetch_report_data.py --dataset paydown
  python scripts/generate_report.py --month 2026-06
  python scripts/generate_report.py --month 2026-06 --preview
  ```

  Output defaults to `reports/monarch_report_YYYY-MM.pdf`. Design lives in `reports/template/` (`report.css`, `report.html`). One-time setup: `playwright install chromium`.

  The first report section shows credit card & line of credit debt paid off for the month and a trailing month-over-month chart.

`reports/data/`, `reports/build/`, and `reports/*.pdf` are gitignored (personal financial data and build artifacts). `reports/template/` is tracked.

## Development

```bash
pip install -e ".[dev]"
ruff check .        # lint
pytest              # run tests
```

CI runs both on every push and pull request.

## Security notes

- `.mm/` and `*.pickle` are gitignored. Do not commit session files.
- The session pickle contains your auth token. Treat it like a password.

## Troubleshooting

**No session file found** — Run `monarch-login`.

**Monarch Money session is expired or invalid** — Re-run `monarch-login`. It forces a fresh login and overwrites the stale session file.

**Network error / timeout** — Increase `MONARCH_TIMEOUT` (e.g. `60`) for large transaction pulls.

**Import errors** — Ensure the package is installed: `pip install -e .`.
