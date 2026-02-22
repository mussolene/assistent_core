"""Vector RAG skill: add and search в векторной памяти пользователя (MemoryManager по user_id)."""

from __future__ import annotations

import logging
from typing import Any

from assistant.memory.manager import MemoryManager
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class VectorRagSkill(BaseSkill):
    """Работа с векторной памятью в разрезе user_id (долговременный уровень)."""

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "vector_rag"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        user_id = (params.get("user_id") or params.get("user") or "default").strip()
        action = (params.get("action") or "search").lower()
        vector = self._memory.get_vector(user_id)
        if action == "add":
            text = params.get("text") or params.get("content") or ""
            if not text:
                return {"error": "text required", "ok": False}
            metadata = params.get("metadata") or {}
            await self._memory.add_to_vector(user_id, text, metadata)
            return {"ok": True}
        if action == "search":
            query = params.get("query") or params.get("q") or ""
            if not query:
                return {"error": "query required", "ok": False}
            top_k = params.get("top_k") or 5
            hits = vector.search(query, top_k=top_k)
            return {"results": hits, "ok": True}
        return {"error": f"unknown action: {action}", "ok": False}
