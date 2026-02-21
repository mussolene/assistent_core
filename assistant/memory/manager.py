"""Memory Manager: short-term, task, summary, векторная память (3 уровня), данные о пользователе."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from assistant.memory.short_term import ShortTermMemory
from assistant.memory.summary import SummaryMemory
from assistant.memory.task_memory import TaskMemory
from assistant.memory.user_data import UserDataMemory
from assistant.memory.vector import VectorMemory

logger = logging.getLogger(__name__)

VECTOR_LEVEL_SHORT = "short"
VECTOR_LEVEL_MEDIUM = "medium"
VECTOR_LEVEL_LONG = "long"


class MemoryManager:
    """Фасад: кратковременная (short-term), среднесрочная и долговременная векторная память, данные о пользователе."""

    def __init__(
        self,
        redis_url: str,
        short_term_window: int = 10,
        summary_threshold_messages: int = 20,
        vector_top_k: int = 5,
        vector_collection: str = "assistant_memory",
        vector_persist_path: str | Path | None = None,
        vector_persist_dir: str | Path | None = None,
        vector_short_max: int = 100,
        vector_medium_max: int = 500,
    ) -> None:
        self._short = ShortTermMemory(redis_url, window=short_term_window)
        self._task = TaskMemory(redis_url)
        self._summary = SummaryMemory(redis_url)
        self._user_data = UserDataMemory(redis_url)
        base = Path(vector_persist_dir or vector_persist_path or "/tmp/assistant_vectors")
        self._vector_short = VectorMemory(
            collection=f"{vector_collection}_short",
            top_k=vector_top_k,
            persist_path=base / "short.json",
            max_size=vector_short_max,
        )
        self._vector_medium = VectorMemory(
            collection=f"{vector_collection}_medium",
            top_k=vector_top_k,
            persist_path=base / "medium.json",
            max_size=vector_medium_max,
        )
        self._vector_long = VectorMemory(
            collection=f"{vector_collection}_long",
            top_k=vector_top_k,
            persist_path=base / "long.json",
            max_size=None,
        )
        self._summary_threshold = summary_threshold_messages

    async def connect(self) -> None:
        await self._short.connect()
        await self._task.connect()
        await self._summary.connect()
        await self._user_data.connect()

    def get_short_term(self) -> ShortTermMemory:
        return self._short

    def get_task_memory(self) -> TaskMemory:
        return self._task

    def get_summary(self) -> SummaryMemory:
        return self._summary

    def get_vector(self) -> VectorMemory:
        """Возвращает долговременную векторную память (обратная совместимость)."""
        return self._vector_long

    def get_vector_short(self) -> VectorMemory:
        return self._vector_short

    def get_vector_medium(self) -> VectorMemory:
        return self._vector_medium

    def get_vector_long(self) -> VectorMemory:
        return self._vector_long

    def get_user_data_memory(self) -> UserDataMemory:
        return self._user_data

    async def get_context_for_user(
        self,
        user_id: str,
        task_id: str,
        session_id: str = "default",
        include_vector: bool = True,
    ) -> list[dict[str, Any]]:
        """Контекст: summary + short-term + векторные попадания (все 3 уровня) + данные о пользователе."""
        messages = []
        summary = await self._summary.get_summary(user_id, session_id)
        if summary:
            messages.append({"role": "system", "content": f"Previous context summary: {summary}"})
        user_data = await self._user_data.get(user_id)
        if user_data:
            ud_str = " ".join(f"{k}: {v}" for k, v in user_data.items() if v)
            if ud_str:
                messages.append({"role": "system", "content": f"User data: {ud_str}"})
        recent = await self._short.get_messages(user_id, session_id)
        messages.extend(recent)
        if include_vector:
            tool_results = await self._task.get_tool_results(task_id)
            query = " ".join(str(r.get("result", ""))[:200] for r in tool_results[-3:]) if tool_results else ""
            if not query and recent:
                query = " ".join(m.get("content", "")[:150] for m in recent[-2:])
            if query:
                hits = []
                for vec in (self._vector_short, self._vector_medium, self._vector_long):
                    if vec._get_model():
                        hits.extend(vec.search(query, top_k=2))
                hits.sort(key=lambda h: -h.get("score", 0))
                seen = set()
                unique = []
                for h in hits[:6]:
                    t = h.get("text", "")[:100]
                    if t not in seen:
                        seen.add(t)
                        unique.append(h)
                if unique:
                    ref = "Relevant memory:\n" + "\n".join(h["text"] for h in unique)
                    messages.append({"role": "system", "content": ref})
        return messages

    async def append_message(self, user_id: str, role: str, content: str, session_id: str = "default") -> None:
        await self._short.append(user_id, role, content, session_id)

    async def store_task_fact(self, task_id: str, key: str, value: Any) -> None:
        await self._task.set(task_id, key, value)

    async def append_tool_result(self, task_id: str, tool_name: str, result: Any) -> None:
        await self._task.append_tool_result(task_id, tool_name, result)

    async def add_to_vector(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        """Добавить в все три уровня векторной памяти (кратковременная, среднесрочная, долговременная)."""
        meta = dict(metadata or {})
        for level, vec in (
            (VECTOR_LEVEL_SHORT, self._vector_short),
            (VECTOR_LEVEL_MEDIUM, self._vector_medium),
            (VECTOR_LEVEL_LONG, self._vector_long),
        ):
            vec.add(text, {**meta, "level": level})

    def clear_vector(self, level: str | None = None) -> None:
        """Очистить векторную память: short, medium, long или все (level=None)."""
        if level == VECTOR_LEVEL_SHORT or level is None:
            self._vector_short.clear()
        if level == VECTOR_LEVEL_MEDIUM or level is None:
            self._vector_medium.clear()
        if level == VECTOR_LEVEL_LONG or level is None:
            self._vector_long.clear()

    async def get_user_data(self, user_id: str) -> dict[str, Any]:
        """Данные о пользователе (профиль, настройки)."""
        return await self._user_data.get(user_id)

    async def set_user_data(self, user_id: str, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Установить данные о пользователе."""
        await self._user_data.set(user_id, data, **kwargs)

    async def clear_user_data(self, user_id: str) -> None:
        """Очистить данные пользователя."""
        await self._user_data.clear(user_id)
