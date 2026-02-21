"""Tests for dashboard auth: setup, login, logout, redirects."""

from unittest.mock import MagicMock

import pytest

from assistant.dashboard.auth import (
    SESSION_PREFIX,
    USER_PREFIX,
    USERS_SET_KEY,
    _hash_password,
    create_session,
    create_user,
    delete_session,
    get_session,
    get_user,
    setup_done,
    verify_password,
    verify_user,
)


@pytest.fixture
def redis_url():
    try:
        import redis
        r = redis.from_url("redis://localhost:6379/13", decode_responses=True)
        r.ping()
        r.close()
        return "redis://localhost:6379/13"
    except Exception:
        pytest.skip("Redis not available")


def test_hash_password_deterministic_with_salt():
    h1, s1 = _hash_password("secret")
    h2, s2 = _hash_password("secret", bytes.fromhex(s1))
    assert h1 == h2
    assert s1 == s2


def test_verify_password():
    h, s = _hash_password("mypass")
    assert verify_password("mypass", h, s) is True
    assert verify_password("wrong", h, s) is False


def test_setup_done_empty_redis():
    r = MagicMock()
    r.smembers.return_value = set()
    assert setup_done(r) is False


def test_setup_done_has_users():
    r = MagicMock()
    r.smembers.return_value = {"admin"}
    assert setup_done(r) is True


def test_create_user_and_get_user(redis_url):
    try:
        import redis
        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception:
        pytest.skip("Redis not available")
    # Clean test keys
    for key in list(client.scan_iter(USER_PREFIX + "*")) + list(client.scan_iter(SESSION_PREFIX + "*")):
        client.delete(key)
    client.delete(USERS_SET_KEY)
    try:
        create_user(client, "auth_test_user", "pass123", role="owner")
        assert client.sismember(USERS_SET_KEY, "auth_test_user")
        user = get_user(client, "auth_test_user")
        assert user is not None
        assert user.get("role") == "owner"
        u = verify_user(client, "auth_test_user", "pass123")
        assert u is not None
        assert u.get("role") == "owner"
        assert verify_user(client, "auth_test_user", "wrong") is None
        assert verify_user(client, "no_such_user", "pass") is None
    finally:
        client.delete(USER_PREFIX + "auth_test_user")
        client.srem(USERS_SET_KEY, "auth_test_user")
        client.close()


def test_create_user_duplicate_raises(redis_url):
    try:
        import redis
        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception:
        pytest.skip("Redis not available")
    client.delete(USERS_SET_KEY)
    client.delete(USER_PREFIX + "dup_user")
    try:
        create_user(client, "dup_user", "pass", role="viewer")
        with pytest.raises(ValueError, match="already exists"):
            create_user(client, "dup_user", "other", role="owner")
    finally:
        client.delete(USER_PREFIX + "dup_user")
        client.srem(USERS_SET_KEY, "dup_user")
        client.close()


def test_session_roundtrip(redis_url):
    try:
        import redis
        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception:
        pytest.skip("Redis not available")
    for key in client.scan_iter(SESSION_PREFIX + "*"):
        client.delete(key)
    try:
        sid = create_session(client, "sess_user")
        assert sid
        sess = get_session(client, sid)
        assert sess is not None
        assert sess.get("login") == "sess_user"
        delete_session(client, sid)
        assert get_session(client, sid) is None
    finally:
        client.close()


@pytest.fixture
def client():
    from assistant.dashboard.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_setup_page_accessible_without_auth(client, monkeypatch):
    """When no users exist, / is redirected to /setup; /setup is accessible."""
    from unittest.mock import MagicMock
    monkeypatch.setattr("assistant.dashboard.app.get_redis", MagicMock())
    monkeypatch.setattr("assistant.dashboard.app.setup_done", lambda r: False)
    monkeypatch.setattr(
        "assistant.dashboard.app.get_current_user",
        lambda r: None,
    )
    r = client.get("/")
    assert r.status_code == 302
    assert "setup" in r.headers.get("Location", "")
    r2 = client.get("/setup")
    assert r2.status_code == 200
    body = r2.data.decode("utf-8", errors="replace").lower()
    assert "настройка" in body or "owner" in body


def test_setup_creates_owner_and_redirects(client, redis_url, monkeypatch):
    """POST /setup with valid data creates user and redirects to index with cookie."""
    try:
        import redis
        r = redis.from_url(redis_url, decode_responses=True)
        r.ping()
        for k in list(r.scan_iter("assistant:user:*")) + list(r.scan_iter("assistant:session:*")):
            r.delete(k)
        r.delete(USERS_SET_KEY)
        r.close()
    except Exception:
        pytest.skip("Redis not available")
    monkeypatch.setattr("assistant.dashboard.config_store.get_redis_url", lambda: redis_url)
    resp = client.post("/setup", data={
        "login": "setup_owner",
        "password": "securepass123",
        "password2": "securepass123",
    })
    assert resp.status_code == 302
    assert resp.headers.get("Location", "").endswith("/")
    from assistant.dashboard.auth import SESSION_COOKIE_NAME
    assert SESSION_COOKIE_NAME in resp.headers.get("Set-Cookie", "")


