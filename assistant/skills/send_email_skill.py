"""Skill: отправка письма (to, subject, body). Allowlist получателей и rate limit (итерация 4.3)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)

REDIS_RATE_PREFIX = "assistant:email_rate:"
RATE_WINDOW_SEC = 3600
RATE_MAX_PER_WINDOW = 10


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _get_allowed_recipients(redis_url: str) -> list[str]:
    """Список разрешённых email из Redis (EMAIL_ALLOWED_RECIPIENTS) или env. Пустой = разрешены любые."""
    try:
        from assistant.dashboard.config_store import get_config_from_redis_sync

        cfg = get_config_from_redis_sync(redis_url)
        raw = cfg.get("EMAIL_ALLOWED_RECIPIENTS") or os.getenv("EMAIL_ALLOWED_RECIPIENTS") or ""
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(e).strip().lower() for e in raw if str(e).strip() and "@" in str(e)]
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [
                        str(e).strip().lower() for e in data if str(e).strip() and "@" in str(e)
                    ]
            except json.JSONDecodeError:
                return [e.strip().lower() for e in raw.split(",") if e.strip() and "@" in e]
        return []
    except Exception as e:
        logger.debug("get allowed recipients: %s", e)
        return []


class SendEmailSkill(BaseSkill):
    """Отправка email через конфиг дашборда (SMTP/SendGrid). Allowlist и rate limit по user_id."""

    def __init__(self, redis_url: str = "") -> None:
        self._redis_url = redis_url or _get_redis_url()

    @property
    def name(self) -> str:
        return "send_email"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        to = (params.get("to") or params.get("recipient") or "").strip()
        subject = (params.get("subject") or "").strip() or "Assistant"
        body = (params.get("body") or params.get("text") or params.get("content") or "").strip()
        user_id = (params.get("user_id") or params.get("user") or "").strip() or "default"

        if not to or "@" not in to:
            return {"ok": False, "error": "Укажи получателя (to) — корректный email."}
        to_lower = to.lower()

        allowed = _get_allowed_recipients(self._redis_url)
        if allowed and to_lower not in allowed:
            return {"ok": False, "error": "Получатель не в списке разрешённых (allowlist)."}

        try:
            import redis

            client = redis.from_url(self._redis_url, decode_responses=True)
            rate_key = REDIS_RATE_PREFIX + user_id
            n = client.incr(rate_key)
            if n == 1:
                client.expire(rate_key, RATE_WINDOW_SEC)
            client.close()
            if n > RATE_MAX_PER_WINDOW:
                return {
                    "ok": False,
                    "error": f"Превышен лимит отправки писем ({RATE_MAX_PER_WINDOW} в час).",
                }
        except Exception as e:
            logger.warning("send_email rate limit check: %s", e)
            # Продолжаем без rate limit при недоступности Redis

        from assistant.channels.email_adapter import send_email

        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(
            None,
            lambda: send_email(to, subject, body, self._redis_url),
        )
        if not ok:
            return {
                "ok": False,
                "error": "Не удалось отправить письмо (проверь настройки Email в дашборде).",
            }
        return {"ok": True, "message": "Письмо отправлено."}
