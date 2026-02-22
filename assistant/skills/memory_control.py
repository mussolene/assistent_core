"""Skill: очистка и полный сброс памяти (векторная, кратковременная, summary, user_data)."""

from __future__ import annotations

import logging
from typing import Any

from assistant.memory.manager import (
    VECTOR_LEVEL_LONG,
    VECTOR_LEVEL_MEDIUM,
    VECTOR_LEVEL_SHORT,
    MemoryManager,
)
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)

SCOPE_ALL = "all"
SCOPE_VECTOR = "vector"
SCOPE_SHORT_TERM = "short_term"
SCOPE_SUMMARY = "summary"
SCOPE_USER_DATA = "user_data"
SCOPES = (SCOPE_ALL, SCOPE_VECTOR, SCOPE_SHORT_TERM, SCOPE_SUMMARY, SCOPE_USER_DATA)


class MemoryControlSkill(BaseSkill):
    """Очистка векторной памяти по уровню и полный сброс памяти пользователя по scope."""

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_control"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or "").strip().lower()
        user_id = (params.get("user_id") or params.get("user") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id обязателен"}

        if action == "clear_vector":
            level = (params.get("level") or params.get("vector_level") or "all").strip().lower()
            if level not in ("short", "medium", "long", "all", ""):
                return {
                    "ok": False,
                    "error": f"level должен быть short, medium, long или all, получено: {level!r}",
                }
            norm = None if level in ("all", "") else level
            if norm == "short":
                norm = VECTOR_LEVEL_SHORT
            elif norm == "medium":
                norm = VECTOR_LEVEL_MEDIUM
            elif norm == "long":
                norm = VECTOR_LEVEL_LONG
            self._memory.clear_vector(user_id=user_id, level=norm)
            return {
                "ok": True,
                "message": f"Векторная память пользователя очищена: {level or 'все уровни'}",
            }

        if action == "reset_memory":
            scope = (params.get("scope") or SCOPE_ALL).strip().lower()
            if scope not in SCOPES:
                return {"ok": False, "error": f"scope должен быть один из: {', '.join(SCOPES)}"}
            session_id = (params.get("session_id") or "default").strip()
            await self._memory.reset_memory(user_id, scope=scope, session_id=session_id)
            return {"ok": True, "message": f"Память сброшена: scope={scope}"}

        return {
            "ok": False,
            "error": f"Неизвестное действие: {action}. Используйте clear_vector или reset_memory.",
        }
