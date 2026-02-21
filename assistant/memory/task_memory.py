"""Task memory: keyed by task_id. Store intermediate results and tool outputs."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "assistant:task:"
TTL = 3600 * 24  # 24 hours


class TaskMemory:
    """Per-task storage for tool outputs and intermediate state."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()

    def _key(self, task_id: str, suffix: str = "data") -> str:
        return f"{KEY_PREFIX}{task_id}:{suffix}"

    async def set(self, task_id: str, key: str, value: Any) -> None:
        await self.connect()
        k = self._key(task_id, key)
        await self._client.set(k, json.dumps(value), ex=TTL)

    async def get(self, task_id: str, key: str) -> Any | None:
        await self.connect()
        raw = await self._client.get(self._key(task_id, key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def append_tool_result(self, task_id: str, tool_name: str, result: Any) -> None:
        await self.connect()
        list_key = self._key(task_id, "tool_results")
        entry = {"tool": tool_name, "result": result}
        await self._client.rpush(list_key, json.dumps(entry))
        await self._client.expire(list_key, TTL)

    async def get_tool_results(self, task_id: str) -> list[dict[str, Any]]:
        await self.connect()
        raw = await self._client.lrange(self._key(task_id, "tool_results"), 0, -1)
        out = []
        for r in raw:
            try:
                out.append(json.loads(r))
            except json.JSONDecodeError:
                continue
        return out

    async def delete_task(self, task_id: str) -> None:
        await self.connect()
        pattern = self._key(task_id, "*")
        keys = []
        async for key in self._client.scan_iter(match=pattern.replace("*", "*")):
            keys.append(key)
        if keys:
            await self._client.delete(*keys)
