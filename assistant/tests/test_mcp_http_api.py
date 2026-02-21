"""Tests for MCP HTTP API: notify, question, confirmation, replies, events, JSON-RPC base."""

from unittest.mock import patch

import pytest


@pytest.fixture
def client():
    from assistant.dashboard.app import app
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def mcp_auth(monkeypatch):
    """Подмена auth: любой Bearer считается валидным, chat_id = test_chat_123."""
    monkeypatch.setattr(
        "assistant.dashboard.mcp_endpoints.verify_endpoint_secret",
        lambda eid, secret: bool(secret),
    )
    monkeypatch.setattr(
        "assistant.dashboard.mcp_endpoints.get_chat_id_for_endpoint",
        lambda eid: "test_chat_123",
    )


def test_mcp_base_get_unauthorized(client):
    """GET /mcp/v1/agent/<id> без Bearer возвращает 401."""
    r = client.get("/mcp/v1/agent/abc123")
    assert r.status_code == 401
    assert r.get_json().get("error") == "Unauthorized"


def test_mcp_base_get_ok(client, mcp_auth):
    """GET /mcp/v1/agent/<id> с Bearer возвращает links (notify, question, confirmation, replies, events)."""
    r = client.get(
        "/mcp/v1/agent/abc123",
        headers={"Authorization": "Bearer secret123"},
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("protocol") == "mcp"
    assert j.get("endpoint_id") == "abc123"
    links = j.get("links", {})
    assert "notify" in links
    assert "question" in links
    assert "confirmation" in links
    assert "replies" in links
    assert "events" in links
    assert "abc123" in links["notify"]


def test_mcp_base_post_initialize(client, mcp_auth):
    """POST /mcp/v1/agent/<id> JSON-RPC initialize возвращает capabilities."""
    r = client.post(
        "/mcp/v1/agent/abc123",
        headers={"Authorization": "Bearer secret123", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("result", {}).get("capabilities", {}).get("tools") is not None
    assert "serverInfo" in j["result"]


def test_mcp_base_post_tools_list(client, mcp_auth):
    """POST /mcp/v1/agent/<id> tools/list возвращает notify, ask_confirmation, get_user_feedback."""
    r = client.post(
        "/mcp/v1/agent/abc123",
        headers={"Authorization": "Bearer secret123", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert r.status_code == 200
    j = r.get_json()
    tools = j.get("result", {}).get("tools", [])
    names = [t["name"] for t in tools]
    assert "notify" in names
    assert "ask_confirmation" in names
    assert "get_user_feedback" in names


def test_mcp_base_post_tools_call_notify(client, mcp_auth):
    """POST /mcp/v1/agent/<id> tools/call notify вызывает notify_to_chat."""
    with patch("assistant.core.notify.notify_to_chat", return_value=True) as m:
        r = client.post(
            "/mcp/v1/agent/abc123",
            headers={"Authorization": "Bearer secret123", "Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "notify", "arguments": {"message": "Test message"}},
            },
        )
    assert r.status_code == 200
    j = r.get_json()
    assert "result" in j
    assert "Отправлено" in (j["result"].get("content", [{}])[0].get("text", ""))
    m.assert_called_once_with("test_chat_123", "Test message")


def test_mcp_notify_endpoint_ok(client, mcp_auth):
    """POST /mcp/v1/agent/<id>/notify с message возвращает ok."""
    with patch("assistant.core.notify.notify_to_chat", return_value=True):
        r = client.post(
            "/mcp/v1/agent/abc123/notify",
            headers={"Authorization": "Bearer secret123", "Content-Type": "application/json"},
            json={"message": "Hello"},
        )
    assert r.status_code == 200
    assert r.get_json().get("ok") is True


def test_mcp_notify_endpoint_no_message(client, mcp_auth):
    """POST /mcp/v1/agent/<id>/notify без message возвращает 400."""
    r = client.post(
        "/mcp/v1/agent/abc123/notify",
        headers={"Authorization": "Bearer secret123", "Content-Type": "application/json"},
        json={},
    )
    assert r.status_code == 400
    assert r.get_json().get("error", "").lower().find("message") >= 0


def test_mcp_replies_ok(client, mcp_auth):
    """GET /mcp/v1/agent/<id>/replies возвращает replies (пустой список если нет)."""
    with patch("assistant.core.notify.pop_dev_feedback", return_value=[]) as m:
        r = client.get(
            "/mcp/v1/agent/abc123/replies",
            headers={"Authorization": "Bearer secret123"},
        )
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert j.get("replies") == []
    m.assert_called_once_with("test_chat_123")


def test_mcp_replies_unauthorized(client):
    """GET /mcp/v1/agent/<id>/replies без Bearer возвращает 401."""
    r = client.get("/mcp/v1/agent/abc123/replies")
    assert r.status_code == 401


def test_mcp_confirmation_endpoint_ok(client, mcp_auth):
    """POST /mcp/v1/agent/<id>/confirmation шлёт запрос с кнопками и возвращает ok."""
    with patch("assistant.core.notify.send_confirmation_request", return_value=True) as send_conf:
        r = client.post(
            "/mcp/v1/agent/abc123/confirmation",
            headers={"Authorization": "Bearer secret123", "Content-Type": "application/json"},
            json={"message": "Deploy?"},
        )
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert j.get("pending") is True
    send_conf.assert_called_once_with("test_chat_123", "Deploy?")
