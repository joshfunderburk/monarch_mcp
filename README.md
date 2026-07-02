# Monarch Money MCP Server

A local stdio MCP server that wraps the [`monarchmoneycommunity`](https://pypi.org/project/monarchmoneycommunity/) Python library, exposing Monarch Money accounts, transactions, budgets, and more as MCP tools for Cursor and other MCP clients.

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

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a session file (one-time, or when the token expires):

```bash
python login.py
```

This saves your auth session to `.mm/mm_session.pickle` next to the script. Re-running `login.py` always performs a fresh login, so use it any time API calls start failing with a "session is expired" error.

## Run the server

```bash
python server.py
```

The server uses stdio transport by default.

## Cursor configuration

Add this to your Cursor MCP config (`mcp.json`):

```json
{
  "mcpServers": {
    "monarch-money": {
      "command": "/Users/joshfunderburk/Desktop/monarch_mcp/.venv/bin/python",
      "args": ["/Users/joshfunderburk/Desktop/monarch_mcp/server.py"]
    }
  }
}
```

The session file path defaults to `.mm/mm_session.pickle` relative to `server.py`, so `MONARCH_SESSION_FILE` is optional. On Windows, point `command` at `.venv\\Scripts\\python.exe` and use Windows-style paths.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MONARCH_SESSION_FILE` | `<script dir>/.mm/mm_session.pickle` | Path to the saved session pickle |
| `MONARCH_TIMEOUT` | `30` | Timeout in seconds for each Monarch API call |

## Available tools

### Accounts
- `get_accounts`
- `get_account_type_options`
- `get_recent_account_balances`
- `get_account_history`
- `get_account_snapshots_by_type`
- `get_aggregate_snapshots`
- `get_account_holdings`
- `get_institutions`

### Transactions (read)
- `get_transactions`
- `get_transaction_details`
- `get_transaction_splits`
- `find_duplicate_transactions`
- `get_recurring_transactions`
- `get_transactions_summary`

### Transactions (write)
- `create_transaction`
- `update_transaction`
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

### Other
- `get_subscription_details`
- `get_credit_history`
- `request_accounts_refresh_and_wait`
- `is_accounts_refresh_complete`
- `create_manual_account`
- `upload_account_balance_history`

## Security notes

- `.mm/` and `*.pickle` are gitignored. Do not commit session files.
- The session pickle contains your auth token. Treat it like a password.

## Troubleshooting

**No session file found** — Run `python login.py`.

**Monarch Money session is expired or invalid** — Re-run `python login.py`. It forces a fresh login and overwrites the stale session file.

**Network error / timeout** — Increase `MONARCH_TIMEOUT` (e.g. `60`) for large transaction pulls.

**Import errors** — Ensure dependencies are installed: `pip install -r requirements.txt`.
