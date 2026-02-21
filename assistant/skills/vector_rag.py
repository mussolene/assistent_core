"""Vector RAG skill: add and search via Memory Manager's vector store."""

from __future__ import annotations

import logging
from typing import Any

from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class VectorRagSkill(BaseSkill):
    """Delegates to a VectorMemory instance. No direct network."""

    def __init__(self, vector_memory: "assistant.memory.vector.VectorMemory") -> None:
        self._vector = vector_memory

    @property
    def name(self) -> str:
        return "vector_rag"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or "search").lower()
        if action == "add":
            text = params.get("text") or params.get("content") or ""
            if not text:
                return {"error": "text required", "ok": False}
            metadata = params.get("metadata") or {}
            self._vector.add(text, metadata)
            return {"ok": True}
        if action == "search":
            query = params.get("query") or params.get("q") or ""
            if not query:
                return {"error": "query required", "ok": False}
            top_k = params.get("top_k") or 5
            hits = self._vector.search(query, top_k=top_k)
            return {"results": hits, "ok": True}
        return {"error": f"unknown action: {action}", "ok": False}
