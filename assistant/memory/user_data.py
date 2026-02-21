"""Данные о пользователе: профиль, настройки, предпочтения. Хранение в Redis по user_id."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "assistant:user_data:"
DEFAULT_TTL_DAYS = 365 * 2


class UserDataMemory:
    """Ключ-значение по user_id. Используется для данных о пользователе (имя, таймзона, предпочтения)."""

    def __init__(self, redis_url: str, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        self._redis_url = redis_url
        self._ttl = ttl_days * 86400
        self._client: aioredis.Redis | None = None

    def _key(self, user_id: str) -> str:
        return f"{KEY_PREFIX}{user_id}"

    async def connect(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()

    async def get(self, user_id: str) -> dict[str, Any]:
        """Получить все данные пользователя."""
        await self.connect()
        raw = await self._client.get(self._key(user_id))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    async def set(self, user_id: str, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Установить данные. Передаётся dict или ключевые аргументы (объединяются с текущими)."""
        await self.connect()
        key = self._key(user_id)
        current = await self.get(user_id)
        if data is not None:
            current.update(data)
        current.update(kwargs)
        await self._client.set(key, json.dumps(current), ex=self._ttl)

    async def set_one(self, user_id: str, field: str, value: Any) -> None:
        """Установить одно поле."""
        await self.set(user_id, **{field: value})

    async def clear(self, user_id: str) -> None:
        """Очистить все данные пользователя."""
        await self.connect()
        await self._client.delete(self._key(user_id))
        logger.info("User data cleared for user_id=%s", user_id)
