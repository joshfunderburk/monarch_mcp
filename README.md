# Monarch Money MCP Server

A local stdio MCP server that wraps the [`monarchmoneycommunity`](https://pypi.org/project/monarchmoneycommunity/) Python library, exposing Monarch Money accounts, transactions, budgets, and more as MCP tools for Cursor and other MCP clients.

## Prerequisites

- Python 3.10+
- A Monarch Money account

## Setup

1. Create and activate a virtual environment (recommended):

```bash
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a session file (one-time, or when the token expires):

```bash
python login.py
```

This saves your auth session to `.mm/mm_session.pickle`. The session is long-lived; re-run `login.py` if API calls start failing with auth errors.

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
      "command": "C:\\Python313\\python.exe",
      "args": ["c:\\Users\\funde\\Desktop\\monarch_mcp\\server.py"],
      "env": {
        "MONARCH_SESSION_FILE": "c:\\Users\\funde\\Desktop\\monarch_mcp\\.mm\\mm_session.pickle"
      }
    }
  }
}
```

If you use a virtual environment, point `command` to `.venv\\Scripts\\python.exe` instead.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MONARCH_SESSION_FILE` | `.mm/mm_session.pickle` | Path to the saved session pickle |

## Available tools

### Accounts
- `get_accounts`
- `get_account_type_options`
- `get_recent_account_balances`
- `get_account_snapshots_by_type`
- `get_aggregate_snapshots`

### Transactions (read)
- `get_transactions`
- `get_transaction_details`
- `get_transaction_splits`
- `find_duplicate_transactions`

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

**Monarch Money request failed** — The session may have expired. Re-run `python login.py`.

**Import errors** — Ensure dependencies are installed: `pip install -r requirements.txt`.
