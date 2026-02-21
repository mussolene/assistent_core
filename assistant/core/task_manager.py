"""Task Manager: create/update/fetch task by task_id. State in Redis."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

KEY_PREFIX = "assistant:task:"
TTL = 3600 * 24  # 24h


class TaskManager:
    """Central task state in Redis."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()

    def _key(self, task_id: str) -> str:
        return f"{KEY_PREFIX}{task_id}"

    def create_id(self) -> str:
        return str(uuid.uuid4())

    async def create(
        self,
        user_id: str,
        chat_id: str,
        channel: str = "telegram",
        message_id: str = "",
        text: str = "",
        reasoning_requested: bool = False,
        stream: bool = True,
    ) -> str:
        await self.connect()
        task_id = self.create_id()
        task = {
            "task_id": task_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "channel": channel,
            "message_id": message_id,
            "text": text,
            "reasoning_requested": reasoning_requested,
            "stream": stream,
            "state": "received",
            "iteration": 0,
            "tool_results": [],
            "pending_tool_calls": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._client.set(self._key(task_id), json.dumps(task), ex=TTL)
        return task_id

    async def get(self, task_id: str) -> dict[str, Any] | None:
        await self.connect()
        raw = await self._client.get(self._key(task_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def update(self, task_id: str, **fields: Any) -> None:
        await self.connect()
        task = await self.get(task_id)
        if not task:
            return
        task.update(fields)
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._client.set(self._key(task_id), json.dumps(task), ex=TTL)
