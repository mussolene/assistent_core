"""Skill: RAG-поиск по коллекции Qdrant (репо или документы) — итерация 7.2."""

from __future__ import annotations

import logging
import os
from typing import Any

from assistant.core.qdrant_docs import (
    REPO_COLLECTION,
    get_qdrant_collection,
    get_qdrant_url,
    search_qdrant,
)
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SearchReposSkill(BaseSkill):
    """Поиск по проиндексированным репозиториям/документам в Qdrant (RAG)."""

    def __init__(self, redis_url: str = "") -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")

    @property
    def name(self) -> str:
        return "search_repos"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        query = (params.get("query") or params.get("q") or "").strip()
        collection = (params.get("collection") or "").strip()
        top_k = int(params.get("top_k") or params.get("limit") or 5)
        if not query:
            return {"ok": False, "error": "Укажи query (поисковый запрос)."}
        qdrant_url = get_qdrant_url(self._redis_url)
        if not qdrant_url:
            return {"ok": False, "error": "Qdrant не настроен (QDRANT_URL)."}
        if not collection:
            collection = get_qdrant_collection(
                self._redis_url,
                "QDRANT_REPOS_COLLECTION",
                REPO_COLLECTION,
            )
        results = search_qdrant(qdrant_url, collection, query, top_k=top_k)
        return {"ok": True, "results": results, "collection": collection, "query": query}
