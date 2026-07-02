"""Error handling and response shaping for Monarch MCP tools."""

from __future__ import annotations

from functools import wraps
from typing import Any, Awaitable, Callable

import aiohttp
from gql.transport.exceptions import TransportQueryError, TransportServerError
from monarchmoney import RequestFailedException

from monarch_mcp.client import reset_client


def _slim(value: Any) -> Any:
    """Recursively strip GraphQL noise from API responses.

    Removes `__typename` keys and keys with None values, which carry no
    information for tool consumers but inflate every response.
    """
    if isinstance(value, dict):
        return {
            k: _slim(v)
            for k, v in value.items()
            if k != "__typename" and v is not None
        }
    if isinstance(value, list):
        return [_slim(item) for item in value]
    return value


def monarch_tool[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Wrap tool calls to surface Monarch API errors clearly and slim responses."""

    @wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return _slim(await fn(*args, **kwargs))
        except TransportServerError as exc:
            if exc.code in (401, 403):
                reset_client()
                raise RuntimeError(
                    "Monarch Money session is expired or invalid. "
                    "Re-run `python login.py` to create a new session."
                ) from exc
            raise RuntimeError(
                f"Monarch Money server error (HTTP {exc.code}): {exc}"
            ) from exc
        except TransportQueryError as exc:
            raise RuntimeError(
                f"Monarch Money API rejected the request: {exc}"
            ) from exc
        except RequestFailedException as exc:
            raise RuntimeError(f"Monarch Money request failed: {exc}") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise RuntimeError(
                f"Network error talking to Monarch Money: {exc!r}. "
                "If this is a timeout, raise MONARCH_TIMEOUT."
            ) from exc

    return wrapper
