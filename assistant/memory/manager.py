"""Memory Manager: single facade for short-term, task, summary, and vector memory."""

from __future__ import annotations

import logging
from typing import Any

from assistant.memory.short_term import ShortTermMemory
from assistant.memory.task_memory import TaskMemory
from assistant.memory.summary import SummaryMemory
from assistant.memory.vector import VectorMemory

logger = logging.getLogger(__name__)


class MemoryManager:
    """Get context for user/session/task; append message; store task fact. Minimize token usage."""

    def __init__(
        self,
        redis_url: str,
        short_term_window: int = 10,
        summary_threshold_messages: int = 20,
        vector_top_k: int = 5,
        vector_collection: str = "assistant_memory",
        vector_persist_path: str | None = None,
    ) -> None:
        self._short = ShortTermMemory(redis_url, window=short_term_window)
        self._task = TaskMemory(redis_url)
        self._summary = SummaryMemory(redis_url)
        self._vector = VectorMemory(collection=vector_collection, top_k=vector_top_k, persist_path=vector_persist_path)
        self._summary_threshold = summary_threshold_messages

    async def connect(self) -> None:
        await self._short.connect()
        await self._task.connect()
        await self._summary.connect()

    def get_short_term(self) -> ShortTermMemory:
        return self._short

    def get_task_memory(self) -> TaskMemory:
        return self._task

    def get_summary(self) -> SummaryMemory:
        return self._summary

    def get_vector(self) -> VectorMemory:
        return self._vector

    async def get_context_for_user(
        self,
        user_id: str,
        task_id: str,
        session_id: str = "default",
        include_vector: bool = True,
    ) -> list[dict[str, Any]]:
        """Build message list for the model: summary (if any) + short-term + optional vector hits."""
        messages = []
        summary = await self._summary.get_summary(user_id, session_id)
        if summary:
            messages.append({"role": "system", "content": f"Previous context summary: {summary}"})
        recent = await self._short.get_messages(user_id, session_id)
        messages.extend(recent)
        if include_vector and self._vector._get_model():
            tool_results = await self._task.get_tool_results(task_id)
            if tool_results:
                query = " ".join(str(r.get("result", ""))[:200] for r in tool_results[-3:])
                if query:
                    hits = self._vector.search(query, top_k=3)
                    if hits:
                        ref = "Relevant memory:\n" + "\n".join(h["text"] for h in hits)
                        messages.append({"role": "system", "content": ref})
        return messages

    async def append_message(self, user_id: str, role: str, content: str, session_id: str = "default") -> None:
        await self._short.append(user_id, role, content, session_id)

    async def store_task_fact(self, task_id: str, key: str, value: Any) -> None:
        await self._task.set(task_id, key, value)

    async def append_tool_result(self, task_id: str, tool_name: str, result: Any) -> None:
        await self._task.append_tool_result(task_id, tool_name, result)

    async def add_to_vector(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        self._vector.add(text, metadata)
