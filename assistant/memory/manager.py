"""Memory Manager: short-term, task, summary, векторная память (3 уровня) в разрезе пользователя, данные о пользователе."""

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


def _safe_user_dir(name: str) -> str:
    """Имя поддиректории для user_id: убираем слэши и ограничиваем длину."""
    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:64]
    return safe or "default"


class MemoryManager:
    """Фасад: кратковременная, summary, векторная память (3 уровня) и user_data — всё в разрезе user_id."""

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
        vector_model_name: str = "all-MiniLM-L6-v2",
        vector_model_path: str | Path | None = None,
    ) -> None:
        self._short = ShortTermMemory(redis_url, window=short_term_window)
        self._task = TaskMemory(redis_url)
        self._summary = SummaryMemory(redis_url)
        self._user_data = UserDataMemory(redis_url)
        self._base_path = Path(
            vector_persist_dir or vector_persist_path or "/tmp/assistant_vectors"
        )
        self._vector_top_k = vector_top_k
        self._vector_collection = vector_collection
        self._vector_short_max = vector_short_max
        self._vector_medium_max = vector_medium_max
        self._vector_model_name = vector_model_name
        self._vector_model_path = vector_model_path
        self._vector_cache: dict[tuple[str, str], VectorMemory] = {}
        self._summary_threshold = summary_threshold_messages

    def _get_vector_memory(self, user_id: str, level: str) -> VectorMemory:
        """Векторное хранилище для пользователя и уровня (short/medium/long). Кэш по (user_id, level)."""
        uid = _safe_user_dir(user_id or "default")
        key = (uid, level)
        if key not in self._vector_cache:
            path = self._base_path / uid / f"{level}.json"
            max_size = (
                self._vector_short_max
                if level == VECTOR_LEVEL_SHORT
                else (self._vector_medium_max if level == VECTOR_LEVEL_MEDIUM else None)
            )
            self._vector_cache[key] = VectorMemory(
                collection=f"{self._vector_collection}_{uid}_{level}",
                top_k=self._vector_top_k,
                persist_path=path,
                max_size=max_size,
                model_name=self._vector_model_name,
                model_path=self._vector_model_path,
            )
        return self._vector_cache[key]

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

    def get_vector(self, user_id: str = "default") -> VectorMemory:
        """Долговременная векторная память для пользователя (для skill vector_rag)."""
        return self._get_vector_memory(user_id, VECTOR_LEVEL_LONG)

    def get_vector_short(self, user_id: str = "default") -> VectorMemory:
        return self._get_vector_memory(user_id, VECTOR_LEVEL_SHORT)

    def get_vector_medium(self, user_id: str = "default") -> VectorMemory:
        return self._get_vector_memory(user_id, VECTOR_LEVEL_MEDIUM)

    def get_vector_long(self, user_id: str = "default") -> VectorMemory:
        return self._get_vector_memory(user_id, VECTOR_LEVEL_LONG)

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
            query = (
                " ".join(str(r.get("result", ""))[:200] for r in tool_results[-3:])
                if tool_results
                else ""
            )
            if not query and recent:
                query = " ".join(m.get("content", "")[:150] for m in recent[-2:])
            if query:
                hits = []
                for level in (VECTOR_LEVEL_SHORT, VECTOR_LEVEL_MEDIUM, VECTOR_LEVEL_LONG):
                    vec = self._get_vector_memory(user_id, level)
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

    async def append_message(
        self, user_id: str, role: str, content: str, session_id: str = "default"
    ) -> None:
        await self._short.append(user_id, role, content, session_id)

    async def store_task_fact(self, task_id: str, key: str, value: Any) -> None:
        await self._task.set(task_id, key, value)

    async def append_tool_result(self, task_id: str, tool_name: str, result: Any) -> None:
        await self._task.append_tool_result(task_id, tool_name, result)

    async def add_to_vector(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Добавить в три уровня векторной памяти пользователя."""
        meta = dict(metadata or {})
        for level in (VECTOR_LEVEL_SHORT, VECTOR_LEVEL_MEDIUM, VECTOR_LEVEL_LONG):
            self._get_vector_memory(user_id, level).add(text, {**meta, "level": level})

    def clear_vector(self, user_id: str | None = None, level: str | None = None) -> None:
        """
        Очистить векторную память.
        user_id: конкретный пользователь или None — очистить для всех известных (по кэшу) и файлам в base_path.
        level: short, medium, long или None — все уровни.
        """
        levels = [VECTOR_LEVEL_SHORT, VECTOR_LEVEL_MEDIUM, VECTOR_LEVEL_LONG]
        if level is not None:
            levels = [level]
        if user_id is not None:
            uid = _safe_user_dir(user_id)
            for lev in levels:
                key = (uid, lev)
                if key in self._vector_cache:
                    self._vector_cache[key].clear()
                    logger.info("Vector memory cleared: user_id=%s level=%s", uid, lev)
                else:
                    path = self._base_path / uid / f"{lev}.json"
                    if path.exists():
                        path.unlink()
                        logger.info("Vector memory file removed: %s", path)
        else:
            for uid, lev in list(self._vector_cache.keys()):
                if lev in levels:
                    self._vector_cache[(uid, lev)].clear()
            for lev in levels:
                if self._base_path.exists():
                    for sub in self._base_path.iterdir():
                        if sub.is_dir():
                            f = sub / f"{lev}.json"
                            if f.exists():
                                f.unlink()
                                logger.info("Vector memory file removed: %s", f)

    def clear_vector_user(self, user_id: str, level: str | None = None) -> None:
        """Очистить векторную память одного пользователя (short, medium, long или все)."""
        self.clear_vector(user_id=user_id, level=level)

    async def clear_short_term(self, user_id: str, session_id: str = "default") -> None:
        """Очистить кратковременную память (последние N сообщений) для пользователя/сессии."""
        await self._short.clear(user_id, session_id)

    async def reset_memory(
        self,
        user_id: str,
        scope: str = "all",
        session_id: str = "default",
    ) -> None:
        """
        Полный или выборочный сброс памяти по пользователю.
        scope: all | vector | short_term | summary | user_data
        """
        if scope in ("all", "vector"):
            self.clear_vector(user_id=user_id, level=None)
        if scope in ("all", "short_term"):
            await self._short.clear(user_id, session_id)
        if scope in ("all", "summary"):
            await self._summary.clear(user_id, session_id)
        if scope in ("all", "user_data"):
            await self._user_data.clear(user_id)
        logger.info("Memory reset: user_id=%s scope=%s", user_id, scope)

    async def get_user_data(self, user_id: str) -> dict[str, Any]:
        """Данные о пользователе (профиль, настройки)."""
        return await self._user_data.get(user_id)

    async def set_user_data(
        self, user_id: str, data: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """Установить данные о пользователе."""
        await self._user_data.set(user_id, data, **kwargs)

    async def clear_user_data(self, user_id: str) -> None:
        """Очистить данные пользователя."""
        await self._user_data.clear(user_id)
