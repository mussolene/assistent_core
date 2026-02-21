"""Tests for MCP endpoints (create, verify, list, delete)."""

from unittest.mock import MagicMock, patch

import pytest

from assistant.dashboard import mcp_endpoints


def test_create_endpoint_returns_id_and_secret():
    with patch("redis.from_url") as m:
        r = MagicMock()
        m.return_value = r
        with patch("assistant.dashboard.mcp_endpoints._redis_url", return_value="redis://localhost/0"):
            eid, secret = mcp_endpoints.create_endpoint("Test", "12345")
    assert len(eid) == 16
    assert len(secret) > 20
    r.sadd.assert_called_once()
    r.set.assert_called()


def test_verify_endpoint_secret_invalid():
    with patch("assistant.dashboard.mcp_endpoints.get_endpoint", return_value={"secret_hash": "abc", "chat_id": "1"}):
        with patch("assistant.dashboard.mcp_endpoints._hash_secret", return_value="other"):
            assert mcp_endpoints.verify_endpoint_secret("e1", "wrong") is False


def test_list_endpoints_empty():
    r = MagicMock()
    r.smembers.return_value = set()
    r.close = MagicMock()
    with patch("redis.from_url", return_value=r):
        with patch("assistant.dashboard.mcp_endpoints._redis_url", return_value="redis://localhost/0"):
            out = mcp_endpoints.list_endpoints()
    assert out == []
