"""Google Calendar: OAuth2 и Calendar API v3 (создание событий)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

REDIS_KEY_CALENDAR_TOKENS = "assistant:integration:calendar:tokens"
AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _load_tokens() -> dict[str, Any] | None:
    """Загрузить токены из Redis. Возвращает None, если не настроено."""
    try:
        import redis

        client = redis.from_url(_get_redis_url(), decode_responses=True)
        raw = client.get(REDIS_KEY_CALENDAR_TOKENS)
        client.close()
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("calendar _load_tokens: %s", e)
        return None


def _save_tokens(data: dict[str, Any]) -> None:
    try:
        import redis

        client = redis.from_url(_get_redis_url(), decode_responses=True)
        client.set(REDIS_KEY_CALENDAR_TOKENS, json.dumps(data))
        client.close()
    except Exception as e:
        logger.exception("calendar _save_tokens: %s", e)
        raise


def calendar_is_configured() -> bool:
    """Проверка: заданы ли client_id и сохранены ли токены."""
    client_id = (os.getenv("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    if not client_id:
        return False
    tokens = _load_tokens()
    return bool(tokens and tokens.get("access_token"))


def get_oauth_url(redirect_uri: str) -> str | None:
    """URL для перехода пользователя на авторизацию Google."""
    client_id = (os.getenv("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    if not client_id:
        return None
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code_for_tokens(code: str, redirect_uri: str) -> bool:
    """Обмен code на access_token и refresh_token. Сохраняет в Redis. Возвращает True при успехе."""
    client_id = (os.getenv("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
    if not client_id:
        return False
    import httpx

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        r = httpx.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
        expires_in = body.get("expires_in", 3600)
        payload = {
            "access_token": body.get("access_token"),
            "expires_at": time.time() + expires_in,
        }
        if body.get("refresh_token"):
            payload["refresh_token"] = body["refresh_token"]
        _save_tokens(payload)
        return True
    except Exception as e:
        logger.exception("calendar exchange_code: %s", e)
        return False


def _refresh_access_token() -> str | None:
    """Обновить access_token по refresh_token. Возвращает новый access_token или None."""
    tokens = _load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return None
    client_id = (os.getenv("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
    if not client_id:
        return None
    import httpx

    try:
        r = httpx.post(
            TOKEN_URL,
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
        tokens["expires_at"] = time.time() + expires_in
        _save_tokens(tokens)
        return tokens.get("access_token")
    except Exception as e:
        logger.warning("calendar refresh token: %s", e)
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


def add_calendar_event(
    title: str,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Добавить событие в Google Calendar (primary). start_iso/end_iso — ISO datetime или date (YYYY-MM-DD)."""
    if not title or not str(title).strip():
        return {"ok": False, "error": "Укажите title события."}
    if not calendar_is_configured():
        return {
            "ok": False,
            "error": "Google Calendar не подключен. Задайте GOOGLE_CALENDAR_CLIENT_ID и GOOGLE_CALENDAR_CLIENT_SECRET в .env и выполните OAuth в дашборде → Интеграции.",
        }
    token = _get_access_token()
    if not token:
        return {"ok": False, "error": "Не удалось получить токен Calendar. Повторите подключение в дашборде."}

    # Формат для API: dateTime с timeZone или date для целого дня
    def _event_time(iso: str | None) -> dict[str, str] | None:
        if not iso or not str(iso).strip():
            return None
        s = str(iso).strip()
        if "T" in s:
            return {"dateTime": s.replace("Z", "+00:00") if s.endswith("Z") else s, "timeZone": "UTC"}
        return {"date": s[:10]}

    start = _event_time(start_iso)
    end = _event_time(end_iso)
    if not start:
        # По умолчанию: сейчас и через 1 час
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        start = {"dateTime": now.isoformat(), "timeZone": "UTC"}
        end = {"dateTime": (now + datetime.timedelta(hours=1)).isoformat(), "timeZone": "UTC"}
    elif not end:
        if "dateTime" in start and start.get("dateTime"):
            import datetime
            try:
                dt = datetime.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                end_dt = dt + datetime.timedelta(hours=1)
                end = {"dateTime": end_dt.isoformat(), "timeZone": "UTC"}
            except ValueError:
                end = start
        else:
            end = start

    body = {"summary": title.strip()}
    if start:
        body["start"] = start
    if end:
        body["end"] = end
    if description and str(description).strip():
        body["description"] = str(description).strip()

    import httpx

    try:
        r = httpx.post(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15.0,
        )
        r.raise_for_status()
        event = r.json()
        return {
            "ok": True,
            "event_id": event.get("id"),
            "html_link": event.get("htmlLink"),
            "summary": title.strip(),
        }
    except Exception as e:
        logger.exception("add_calendar_event: %s", e)
        return {"ok": False, "error": str(e)}
