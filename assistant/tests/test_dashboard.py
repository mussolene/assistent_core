"""Tests for dashboard config store and app API."""

import pytest

from assistant.dashboard.config_store import (
    REDIS_PREFIX,
    MCP_SERVERS_KEY,
    PAIRING_MODE_KEY,
    set_config_in_redis_sync,
    get_config_from_redis_sync,
    add_telegram_allowed_user,
)


def _redis_available():
    try:
        import redis
        r = redis.from_url("redis://localhost:6379/13", decode_responses=True)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


@pytest.fixture
def redis_url():
    if not _redis_available():
        pytest.skip("Redis not available")
    return "redis://localhost:6379/13"


def test_config_store_roundtrip(redis_url):
    set_config_in_redis_sync(redis_url, "TEST_KEY", "test_value")
    data = get_config_from_redis_sync(redis_url)
    assert data.get("TEST_KEY") == "test_value"
    set_config_in_redis_sync(redis_url, "TEST_KEY", "")
    data2 = get_config_from_redis_sync(redis_url)
    assert data2.get("TEST_KEY") == ""


def test_config_store_mcp_servers_roundtrip(redis_url):
    servers = [{"name": "m1", "url": "http://localhost:3000"}, {"name": "m2", "url": "http://localhost:3001"}]
    set_config_in_redis_sync(redis_url, MCP_SERVERS_KEY, servers)
    data = get_config_from_redis_sync(redis_url)
    assert data.get(MCP_SERVERS_KEY) == servers


def test_config_store_pairing_mode(redis_url):
    set_config_in_redis_sync(redis_url, PAIRING_MODE_KEY, "true")
    data = get_config_from_redis_sync(redis_url)
    assert data.get(PAIRING_MODE_KEY) == "true"


@pytest.mark.asyncio
async def test_add_telegram_allowed_user(redis_url):
    set_config_in_redis_sync(redis_url, "TELEGRAM_ALLOWED_USER_IDS", [111])
    await add_telegram_allowed_user(redis_url, 222)
    data = get_config_from_redis_sync(redis_url)
    ids = data.get("TELEGRAM_ALLOWED_USER_IDS", [])
    assert isinstance(ids, list)
    assert 111 in ids and 222 in ids
    await add_telegram_allowed_user(redis_url, 222)
    data2 = get_config_from_redis_sync(redis_url)
    assert len(data2.get("TELEGRAM_ALLOWED_USER_IDS", [])) == 2


@pytest.fixture
def client():
    from assistant.dashboard.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_api_test_bot_no_token(monkeypatch, client):
    """Dashboard API test-bot returns error when token not set."""
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    r = client.post("/api/test-bot")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is False
    assert "token" in (j.get("error") or "").lower() or "set" in (j.get("error") or "").lower()


def test_api_test_bot_mock(monkeypatch, client):
    """Dashboard API test-bot returns ok when getMe succeeds."""
    monkeypatch.setattr(
        "assistant.dashboard.app.get_config_from_redis_sync",
        lambda url: {"TELEGRAM_BOT_TOKEN": "123:ABC"},
    )
    import httpx
    def fake_get(*a, **kw):
        return httpx.Response(200, json={"ok": True, "result": {"username": "test_bot"}})
    monkeypatch.setattr("httpx.get", fake_get)
    r = client.post("/api/test-bot")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert j.get("username") == "test_bot"


def test_api_monitor(client):
    """Dashboard API monitor returns dict (may be empty if no Redis)."""
    r = client.get("/api/monitor")
    assert r.status_code == 200
    j = r.get_json()
    assert isinstance(j, dict)


def test_api_test_model_returns_json(monkeypatch, client):
    """Dashboard API test-model returns JSON with ok key (may fail without real model)."""
    monkeypatch.setattr(
        "assistant.dashboard.app.get_config_from_redis_sync",
        lambda url: {"OPENAI_BASE_URL": "http://127.0.0.1:9999/v1", "MODEL_NAME": "x", "OPENAI_API_KEY": "k"},
    )
    r = client.post("/api/test-model")
    assert r.status_code == 200
    j = r.get_json()
    assert "ok" in j
    if not j["ok"]:
        assert "error" in j
