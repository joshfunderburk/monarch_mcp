"""Tests for response slimming and error mapping."""

import asyncio

import pytest
from gql.transport.exceptions import TransportQueryError, TransportServerError

from monarch.errors import monarch_tool, slim


def test_slim_strips_typename_and_nulls():
    payload = {
        "__typename": "Account",
        "id": "1",
        "notes": None,
        "nested": {"__typename": "Merchant", "name": "Costco", "logo": None},
        "rows": [{"__typename": "Tag", "id": "t1", "color": None}],
    }
    assert slim(payload) == {
        "id": "1",
        "nested": {"name": "Costco"},
        "rows": [{"id": "t1"}],
    }


def test_slim_keeps_falsy_non_null_values():
    assert slim([0, False, "", {"amount": 0.0}]) == [0, False, "", {"amount": 0.0}]


def test_monarch_tool_slims_results():
    @monarch_tool
    async def fake_tool():
        return {"__typename": "X", "value": 1, "empty": None}

    assert asyncio.run(fake_tool()) == {"value": 1}


def test_monarch_tool_maps_expired_session_to_actionable_error():
    @monarch_tool
    async def fake_tool():
        raise TransportServerError("unauthorized", code=401)

    with pytest.raises(RuntimeError, match="session is expired"):
        asyncio.run(fake_tool())


def test_monarch_tool_maps_query_errors():
    @monarch_tool
    async def fake_tool():
        raise TransportQueryError("bad query")

    with pytest.raises(RuntimeError, match="rejected the request"):
        asyncio.run(fake_tool())
