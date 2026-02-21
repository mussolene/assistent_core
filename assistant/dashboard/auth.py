"""Dashboard auth: users and sessions in Redis, role-based access."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from functools import wraps
from typing import Any

from flask import request, redirect, url_for, session as flask_session

logger = logging.getLogger(__name__)

USERS_SET_KEY = "assistant:users"
USER_PREFIX = "assistant:user:"
SESSION_PREFIX = "assistant:session:"
SETUP_DONE_KEY = "assistant:setup_done"
SESSION_TTL = 86400  # 24h
SESSION_COOKIE_NAME = "assistant_sid"
PBKDF2_ITERATIONS = 100_000


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return (hex_hash, hex_salt). If salt is None, generate new."""
    if salt is None:
        salt = secrets.token_bytes(32)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return h.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, stored_salt_hex: str) -> bool:
    """Verify password against stored hash and salt."""
    try:
        salt = bytes.fromhex(stored_salt_hex)
    except ValueError:
        return False
    h, _ = _hash_password(password, salt)
    return secrets.compare_digest(h, stored_hash)


def get_redis():
    """Sync Redis client for auth (used in request context)."""
    import redis
    from assistant.dashboard.config_store import get_redis_url
    return redis.from_url(get_redis_url(), decode_responses=True)


def setup_done(redis_client: Any) -> bool:
    """True if at least one user exists (setup completed)."""
    try:
        logins = redis_client.smembers(USERS_SET_KEY)
        return len(logins) > 0
    except Exception:
        return False


def create_user(redis_client: Any, login: str, password: str, role: str = "viewer") -> None:
    """Create user. Raises if login exists. Role: owner, operator, viewer."""
    if redis_client.sismember(USERS_SET_KEY, login):
        raise ValueError("User already exists")
    password_hash, salt_hex = _hash_password(password)
    data = {
        "password_hash": password_hash,
        "salt": salt_hex,
        "role": role,
        "display_name": login,
        "created_at": "",  # optional, skip for minimal
    }
    key = USER_PREFIX + login
    redis_client.set(key, json.dumps(data))
    redis_client.sadd(USERS_SET_KEY, login)


def get_user(redis_client: Any, login: str) -> dict[str, Any] | None:
    """Get user by login. Returns dict with role, display_name, etc. (no password_hash in logic)."""
    raw = redis_client.get(USER_PREFIX + login)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def verify_user(redis_client: Any, login: str, password: str) -> dict[str, Any] | None:
    """Verify login/password. Returns user dict (with role) or None."""
    data = get_user(redis_client, login)
    if not data:
        return None
    if not verify_password(password, data["password_hash"], data["salt"]):
        return None
    return {k: v for k, v in data.items() if k not in ("password_hash", "salt")}


def create_session(redis_client: Any, login: str) -> str:
    """Create session for login. Returns session_id."""
    sid = secrets.token_urlsafe(32)
    key = SESSION_PREFIX + sid
    redis_client.setex(key, SESSION_TTL, json.dumps({"login": login}))
    return sid


def get_session(redis_client: Any, session_id: str) -> dict[str, Any] | None:
    """Get session payload (login). Refreshes TTL on access."""
    if not session_id:
        return None
    key = SESSION_PREFIX + session_id
    raw = redis_client.get(key)
    if not raw:
        return None
    redis_client.expire(key, SESSION_TTL)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete_session(redis_client: Any, session_id: str) -> None:
    """Remove session (logout)."""
    if session_id:
        redis_client.delete(SESSION_PREFIX + session_id)


def get_current_user(redis_client: Any) -> dict[str, Any] | None:
    """Current user from request session cookie, or None."""
    sid = request.cookies.get(SESSION_COOKIE_NAME) if request else None
    if not sid:
        return None
    sess = get_session(redis_client, sid)
    if not sess:
        return None
    login = sess.get("login")
    if not login:
        return None
    user = get_user(redis_client, login)
    if not user:
        return None
    return {"login": login, "role": user.get("role", "viewer"), "display_name": user.get("display_name", login)}


def require_auth(f):
    """Decorator: redirect to login or setup if not authenticated."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        redis_client = get_redis()
        if not setup_done(redis_client):
            if request.path.startswith("/setup") or request.path == "/login":
                return f(*args, **kwargs)
            return redirect(url_for("setup"))
        user = get_current_user(redis_client)
        if user:
            return f(*args, **kwargs)
        if request.path.startswith("/setup"):
            return f(*args, **kwargs)
        return redirect(url_for("login", next=request.url))
    return wrapped


def require_role(*allowed_roles: str):
    """Decorator: require auth and one of allowed roles."""
    def deco(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            redis_client = get_redis()
            if not setup_done(redis_client):
                return redirect(url_for("setup"))
            user = get_current_user(redis_client)
            if not user:
                return redirect(url_for("login", next=request.url))
            if user.get("role") not in allowed_roles:
                from flask import abort
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return deco
