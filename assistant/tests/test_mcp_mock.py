"""Tests for mock MCP server and MCP config with server URL/args."""

import httpx
import pytest

from assistant.tests.mcp_mock_server import run_mock_mcp_server


@pytest.fixture
def mock_mcp_server():
    server, port = run_mock_mcp_server(port=0)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_mock_mcp_tools(mock_mcp_server):
    """Mock MCP server returns tools list at GET /tools."""
    r = httpx.get(f"{mock_mcp_server}/tools", timeout=2.0)
    assert r.status_code == 200
    data = r.json()
    assert "tools" in data
    assert len(data["tools"]) >= 1
    assert data["tools"][0].get("name") == "test_tool"


def test_mock_mcp_call_echo_args(mock_mcp_server):
    """Mock MCP server POST /call echoes args."""
    r = httpx.post(
        f"{mock_mcp_server}/call",
        json={"tool": "test_tool", "args": {"key": "value"}},
        timeout=2.0,
    )
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert data.get("args_received") == {"key": "value"}
