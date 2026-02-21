"""Short-term memory: last N messages per user/session in Redis."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "assistant:short_term:"
DEFAULT_WINDOW = 10


class ShortTermMemory:
    """Last N messages per user/session. Capped list in Redis."""

    def __init__(self, redis_url: str, window: int = DEFAULT_WINDOW) -> None:
        self._redis_url = redis_url
        self._window = window
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()

    def _key(self, user_id: str, session_id: str = "default") -> str:
        return f"{KEY_PREFIX}{user_id}:{session_id}"

    async def append(
        self, user_id: str, role: str, content: str, session_id: str = "default"
    ) -> None:
        await self.connect()
        key = self._key(user_id, session_id)
        msg = {"role": role, "content": content}
        pipe = self._client.pipeline()
        pipe.rpush(key, json.dumps(msg))
        pipe.ltrim(key, -self._window, -1)
        pipe.expire(key, 86400 * 7)  # 7 days
        await pipe.execute()

    async def get_messages(self, user_id: str, session_id: str = "default") -> list[dict[str, Any]]:
        await self.connect()
        key = self._key(user_id, session_id)
        raw = await self._client.lrange(key, 0, -1)
        out = []
        for r in raw:
            try:
                out.append(json.loads(r))
            except json.JSONDecodeError:
                continue
        return out

    async def clear(self, user_id: str, session_id: str = "default") -> None:
        await self.connect()
        await self._client.delete(self._key(user_id, session_id))
