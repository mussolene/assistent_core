"""Compressed/summary memory: summarize older messages to minimize tokens."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "assistant:summary:"
TTL = 86400 * 30  # 30 days


class SummaryMemory:
    """Store compressed summaries per user/session. No LLM call here; caller provides summary text."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()

    def _key(self, user_id: str, session_id: str = "default") -> str:
        return f"{KEY_PREFIX}{user_id}:{session_id}"

    async def set_summary(self, user_id: str, summary: str, session_id: str = "default") -> None:
        await self.connect()
        await self._client.set(self._key(user_id, session_id), summary, ex=TTL)

    async def get_summary(self, user_id: str, session_id: str = "default") -> str | None:
        await self.connect()
        return await self._client.get(self._key(user_id, session_id))

    async def clear(self, user_id: str, session_id: str = "default") -> None:
        """Очистить сжатое резюме для пользователя/сессии."""
        await self.connect()
        await self._client.delete(self._key(user_id, session_id))
        logger.info("Summary memory cleared for user_id=%s session_id=%s", user_id, session_id)
