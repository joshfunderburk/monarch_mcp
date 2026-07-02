"""MonarchMoney client lifecycle and configuration."""

from __future__ import annotations

import os
import pickle

from monarchmoney import LoginFailedException, MonarchMoney

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
# src layout: the repo root is two levels above the package directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(_PACKAGE_DIR))
DEFAULT_SESSION_FILE = os.path.join(_REPO_ROOT, ".mm", "mm_session.pickle")
SESSION_FILE = os.environ.get("MONARCH_SESSION_FILE", DEFAULT_SESSION_FILE)
try:
    API_TIMEOUT = int(os.environ.get("MONARCH_TIMEOUT", "30"))
except ValueError as exc:
    raise RuntimeError(
        f"MONARCH_TIMEOUT must be an integer number of seconds, got "
        f"{os.environ.get('MONARCH_TIMEOUT')!r}."
    ) from exc

_client: MonarchMoney | None = None


def reset_client() -> None:
    """Clear the cached client so the next call reloads the session."""
    global _client
    _client = None


def get_client() -> MonarchMoney:
    """Return a lazily initialized MonarchMoney client with a loaded session."""
    global _client
    if _client is not None:
        return _client

    if not os.path.exists(SESSION_FILE):
        raise RuntimeError(
            f"No session file found at {SESSION_FILE}. "
            "Run `monarch-login` to create one."
        )

    mm = MonarchMoney(session_file=SESSION_FILE, timeout=API_TIMEOUT)
    try:
        mm.load_session()
    except (LoginFailedException, pickle.UnpicklingError, EOFError) as exc:
        raise RuntimeError(
            f"Session file {SESSION_FILE} is invalid or corrupt: {exc}. "
            "Re-run `monarch-login` to create a new one."
        ) from exc
    _client = mm
    return _client
