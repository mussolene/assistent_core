"""Tests for dashboard config store and app API."""

import pytest

from assistant.dashboard.config_store import (
    MCP_SERVERS_KEY,
    PAIRING_CODE_PREFIX,
    PAIRING_MODE_KEY,
    add_telegram_allowed_user,
    consume_pairing_code,
    create_pairing_code,
    get_config_from_redis_sync,
    set_config_in_redis_sync,
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
    servers = [
        {"name": "m1", "url": "http://localhost:3000"},
        {"name": "m2", "url": "http://localhost:3001"},
    ]
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


@pytest.fixture
def auth_mock(monkeypatch):
    """Bypass auth: setup_done True, current user owner. Use with client for protected routes."""
    monkeypatch.setattr("assistant.dashboard.app.setup_done", lambda r: True)
    monkeypatch.setattr(
        "assistant.dashboard.app.get_current_user",
        lambda r: {"login": "test", "role": "owner", "display_name": "test"},
    )
    from unittest.mock import MagicMock

    monkeypatch.setattr("assistant.dashboard.app.get_redis", lambda: MagicMock())


def test_api_test_bot_no_token(monkeypatch, client, auth_mock):
    """Dashboard API test-bot returns error when token not set."""
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    r = client.post("/api/test-bot")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is False
    assert "token" in (j.get("error") or "").lower() or "set" in (j.get("error") or "").lower()


def test_api_test_bot_mock(monkeypatch, client, auth_mock):
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


def test_api_monitor(client, auth_mock):
    """Dashboard API monitor returns dict (may be empty if no Redis)."""
    r = client.get("/api/monitor")
    assert r.status_code == 200
    j = r.get_json()
    assert isinstance(j, dict)


def test_api_cloned_repos_returns_ok(client, auth_mock, monkeypatch):
    """GET /api/cloned-repos returns ok, repos list and workspace_dir."""
    monkeypatch.setattr("assistant.dashboard.app._get_workspace_dir", lambda: "")
    r = client.get("/api/cloned-repos")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert "repos" in j
    assert j.get("workspace_dir") is None


def test_api_cloned_repos_with_repos(client, auth_mock, monkeypatch):
    """GET /api/cloned-repos with mocked list returns repos."""
    monkeypatch.setattr("assistant.dashboard.app._get_workspace_dir", lambda: "/tmp")
    monkeypatch.setattr(
        "assistant.skills.git.list_cloned_repos_sync",
        lambda w: [{"path": "my-repo", "remote_url": "https://github.com/o/r"}],
    )
    r = client.get("/api/cloned-repos")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert len(j.get("repos", [])) == 1
    assert j["repos"][0]["path"] == "my-repo"
    assert j["repos"][0]["remote_url"] == "https://github.com/o/r"


def test_api_test_model_returns_json(monkeypatch, client, auth_mock):
    """Dashboard API test-model returns JSON with ok key (may fail without real model)."""
    monkeypatch.setattr(
        "assistant.dashboard.app.get_config_from_redis_sync",
        lambda url: {
            "OPENAI_BASE_URL": "http://127.0.0.1:9999/v1",
            "MODEL_NAME": "x",
            "OPENAI_API_KEY": "k",
        },
    )
    r = client.post("/api/test-model")
    assert r.status_code == 200
    j = r.get_json()
    assert "ok" in j
    if not j["ok"]:
        assert "error" in j


def test_save_model_redirect(monkeypatch, client, auth_mock):
    """save-model redirects to model and saves config."""
    set_calls = []
    monkeypatch.setattr("assistant.dashboard.app.get_redis_url", lambda: "redis://localhost:6379/0")
    monkeypatch.setattr(
        "assistant.dashboard.app.set_config_in_redis_sync",
        lambda url, key, val: set_calls.append((key, val)),
    )
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    r = client.post(
        "/save-model",
        data={
            "openai_base_url": "http://localhost:11434/v1",
            "model_name": "llama",
            "model_fallback_name": "",
            "cloud_fallback_enabled": "",
            "lm_studio_native": "1",
            "openai_api_key": "",
        },
    )
    assert r.status_code == 302
    assert r.headers.get("Location", "").endswith("/model")
    keys_saved = [c[0] for c in set_calls]
    assert "OPENAI_BASE_URL" in keys_saved
    assert "MODEL_NAME" in keys_saved
    assert "LM_STUDIO_NATIVE" in keys_saved


def test_save_mcp_valid(monkeypatch, client, auth_mock):
    """save-mcp with name+url adds server and redirects to mcp."""
    set_calls = []
    monkeypatch.setattr("assistant.dashboard.app.get_redis_url", lambda: "redis://localhost:6379/0")
    monkeypatch.setattr(
        "assistant.dashboard.app.set_config_in_redis_sync",
        lambda url, key, val: set_calls.append((key, val)),
    )
    monkeypatch.setattr(
        "assistant.dashboard.app.get_config_from_redis_sync",
        lambda url: {},
    )
    monkeypatch.setattr("assistant.dashboard.app.load_config", lambda: {"MCP_SERVERS": []})
    r = client.post("/save-mcp", data={"mcp_name": "mock-mcp", "mcp_url": "http://localhost:3000"})
    assert r.status_code == 302
    assert r.headers.get("Location", "").endswith("/mcp")
    assert len(set_calls) == 1
    assert set_calls[0][0] == MCP_SERVERS_KEY
    assert set_calls[0][1] == [{"name": "mock-mcp", "url": "http://localhost:3000"}]


def test_save_mcp_invalid_json_flash(monkeypatch, client, auth_mock):
    """save-mcp with invalid JSON in args flashes error and redirects to mcp."""
    monkeypatch.setattr("assistant.dashboard.app.get_redis_url", lambda: "redis://localhost:6379/0")
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    monkeypatch.setattr("assistant.dashboard.app.load_config", lambda: {"MCP_SERVERS": []})
    r = client.post(
        "/save-mcp",
        data={"mcp_name": "x", "mcp_url": "http://localhost:3000", "mcp_args": "not json"},
    )
    assert r.status_code == 302
    assert r.headers.get("Location", "").endswith("/mcp")


def test_save_mcp_with_args(monkeypatch, client, auth_mock):
    """save-mcp with valid JSON args stores server with args."""
    set_calls = []
    monkeypatch.setattr("assistant.dashboard.app.get_redis_url", lambda: "redis://localhost:6379/0")
    monkeypatch.setattr(
        "assistant.dashboard.app.set_config_in_redis_sync",
        lambda url, key, val: set_calls.append((key, val)),
    )
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    monkeypatch.setattr("assistant.dashboard.app.load_config", lambda: {"MCP_SERVERS": []})
    r = client.post(
        "/save-mcp",
        data={
            "mcp_name": "with-args",
            "mcp_url": "http://localhost:3000",
            "mcp_args": '{"api_key": "test-key"}',
        },
    )
    assert r.status_code == 302
    assert len(set_calls) == 1
    servers = set_calls[0][1]
    assert len(servers) == 1
    assert servers[0]["name"] == "with-args"
    assert servers[0]["url"] == "http://localhost:3000"
    assert servers[0].get("args") == {"api_key": "test-key"}


def test_create_and_consume_pairing_code(redis_url):
    code, expires = create_pairing_code(redis_url)
    assert len(code) == 6
    assert code.isalnum()
    assert expires == 600
    import redis

    r = redis.from_url(redis_url, decode_responses=True)
    assert r.get(PAIRING_CODE_PREFIX + code) == "1"
    assert consume_pairing_code(redis_url, code) is True
    assert r.get(PAIRING_CODE_PREFIX + code) is None
    assert consume_pairing_code(redis_url, code) is False
    r.close()


def test_api_pairing_code_returns_code_and_link(client, auth_mock, monkeypatch):
    monkeypatch.setattr("assistant.dashboard.app.get_redis_url", lambda: "redis://localhost:6379/0")
    monkeypatch.setattr("assistant.dashboard.app.create_pairing_code", lambda url: ("ABC123", 600))
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    r = client.post("/api/pairing-code")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True
    assert j.get("code") == "ABC123"
    assert j.get("expires_in_sec") == 600


def test_email_page_renders(client, auth_mock, monkeypatch):
    monkeypatch.setattr("assistant.dashboard.app.get_config_from_redis_sync", lambda url: {})
    r = client.get("/email")
    assert r.status_code == 200
    body = r.data.decode("utf-8", errors="replace")
    assert "Email" in body
    assert "email_from" in body or "email_from" in body.lower()


def test_save_email_redirects(client, auth_mock, redis_url, monkeypatch):
    monkeypatch.setattr("assistant.dashboard.app.get_redis_url", lambda: redis_url)
    r = client.post(
        "/save-email",
        data={
            "email_from": "bot@test.local",
            "email_provider": "smtp",
            "email_smtp_port": "587",
        },
    )
    assert r.status_code == 302
    assert "email" in r.headers.get("Location", "")
    data = get_config_from_redis_sync(redis_url)
    assert data.get("EMAIL_FROM") == "bot@test.local"
    assert data.get("EMAIL_PROVIDER") == "smtp"
