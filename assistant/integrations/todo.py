"""Microsoft To-Do: OAuth2 и Graph API (список списков, создание задачи)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

REDIS_KEY_TODO_TOKENS = "assistant:integration:todo:tokens"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTH_BASE = "https://login.microsoftonline.com/common/oauth2/v2.0"
SCOPE = "https://graph.microsoft.com/.default offline_access openid"


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _load_tokens() -> dict[str, Any] | None:
    """Загрузить токены из Redis. Возвращает None, если не настроено."""
    try:
        import redis
        client = redis.from_url(_get_redis_url(), decode_responses=True)
        raw = client.get(REDIS_KEY_TODO_TOKENS)
        client.close()
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("todo _load_tokens: %s", e)
        return None


def _save_tokens(data: dict[str, Any]) -> None:
    try:
        import redis
        client = redis.from_url(_get_redis_url(), decode_responses=True)
        client.set(REDIS_KEY_TODO_TOKENS, json.dumps(data))
        client.close()
    except Exception as e:
        logger.exception("todo _save_tokens: %s", e)
        raise


def todo_is_configured() -> bool:
    """Проверка: заданы ли client_id/secret и сохранены ли токены."""
    client_id = (os.getenv("MS_TODO_CLIENT_ID") or "").strip()
    if not client_id:
        return False
    tokens = _load_tokens()
    return bool(tokens and tokens.get("access_token"))


def get_oauth_url(redirect_uri: str) -> str | None:
    """URL для перехода пользователя на авторизацию Microsoft."""
    client_id = (os.getenv("MS_TODO_CLIENT_ID") or "").strip()
    if not client_id:
        return None
    from urllib.parse import urlencode
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "Tasks.ReadWrite User.Read offline_access openid",
        "response_mode": "query",
    }
    return f"{AUTH_BASE}/authorize?{urlencode(params)}"


def exchange_code_for_tokens(code: str, redirect_uri: str) -> bool:
    """Обмен code на access_token и refresh_token. Сохраняет в Redis. Возвращает True при успехе."""
    client_id = (os.getenv("MS_TODO_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("MS_TODO_CLIENT_SECRET") or "").strip()
    if not client_id:
        return False
    import httpx
    data = {
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        r = httpx.post(
            f"{AUTH_BASE}/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
        expires_in = body.get("expires_in", 3600)
        payload = {
            "access_token": body.get("access_token"),
            "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + expires_in,
        }
        _save_tokens(payload)
        return True
    except Exception as e:
        logger.exception("todo exchange_code: %s", e)
        return False


def _refresh_access_token() -> str | None:
    """Обновить access_token по refresh_token. Возвращает новый access_token или None."""
    tokens = _load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return None
    client_id = (os.getenv("MS_TODO_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("MS_TODO_CLIENT_SECRET") or "").strip()
    if not client_id:
        return None
    import httpx
    try:
        r = httpx.post(
            f"{AUTH_BASE}/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret or "",
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
        expires_in = body.get("expires_in", 3600)
        tokens["access_token"] = body.get("access_token")
        tokens["refresh_token"] = body.get("refresh_token", tokens["refresh_token"])
        tokens["expires_at"] = time.time() + expires_in
        _save_tokens(tokens)
        return tokens.get("access_token")
    except Exception as e:
        logger.warning("todo refresh token: %s", e)
        return None


def _get_access_token() -> str | None:
    """Актуальный access_token (при необходимости обновляет по refresh)."""
    tokens = _load_tokens()
    if not tokens:
        return None
    access = tokens.get("access_token")
    expires_at = tokens.get("expires_at") or 0
    if access and expires_at > time.time() + 60:
        return access
    return _refresh_access_token()


def list_todo_lists() -> dict[str, Any]:
    """Список списков задач To-Do. Возвращает {ok, lists: [{id, displayName}], error?}."""
    if not todo_is_configured():
        return {"ok": False, "error": "Microsoft To-Do не подключен. Задайте MS_TODO_CLIENT_ID и MS_TODO_CLIENT_SECRET в .env и выполните OAuth в дашборде → Интеграции."}
    token = _get_access_token()
    if not token:
        return {"ok": False, "error": "Не удалось получить токен To-Do. Повторите подключение в дашборде."}
    try:
        import httpx
        r = httpx.get(
            f"{GRAPH_BASE}/me/todo/lists",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        lists = [
            {"id": item["id"], "displayName": item.get("displayName", "")}
            for item in data.get("value", [])
        ]
        return {"ok": True, "lists": lists}
    except Exception as e:
        logger.exception("list_todo_lists: %s", e)
        return {"ok": False, "error": str(e)}


def create_task_in_todo(title: str, list_id: str | None = None) -> dict[str, Any]:
    """Создать задачу в Microsoft To-Do. list_id — id списка или None (первый список)."""
    if not title or not (title := str(title).strip()):
        return {"ok": False, "error": "Укажите title задачи."}
    if not todo_is_configured():
        return {"ok": False, "error": "Microsoft To-Do не подключен. Настройте в дашборде → Интеграции."}
    token = _get_access_token()
    if not token:
        return {"ok": False, "error": "Не удалось получить токен To-Do. Повторите подключение."}
    if not list_id:
        lists_result = list_todo_lists()
        if not lists_result.get("ok") or not lists_result.get("lists"):
            return {"ok": False, "error": "Нет доступных списков To-Do или не удалось их загрузить."}
        list_id = lists_result["lists"][0]["id"]
    try:
        import httpx
        r = httpx.post(
            f"{GRAPH_BASE}/me/todo/lists/{list_id}/tasks",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"title": title},
            timeout=15.0,
        )
        r.raise_for_status()
        task = r.json()
        return {"ok": True, "task_id": task.get("id"), "title": title}
    except Exception as e:
        logger.exception("create_task_in_todo: %s", e)
        return {"ok": False, "error": str(e)}
