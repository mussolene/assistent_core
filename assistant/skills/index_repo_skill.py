"""Skill: индексация репозитория в Qdrant — обход файлов, чанки, embedding, upsert (итерация 7.1)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from assistant.core.qdrant_docs import (
    REPO_COLLECTION,
    get_qdrant_url,
    index_repo_to_qdrant,
)
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class IndexRepoSkill(BaseSkill):
    """Индексация каталога репозитория в Qdrant: обход файлов, извлечение текста, чанки, эмбеддинг, upsert."""

    def __init__(self, redis_url: str = "") -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")

    @property
    def name(self) -> str:
        return "index_repo"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        repo_dir = (
            params.get("repo_dir") or params.get("path") or params.get("repo") or ""
        ).strip()
        user_id = (params.get("user_id") or params.get("user") or "default").strip()
        collection = (params.get("collection") or REPO_COLLECTION).strip() or REPO_COLLECTION
        if not repo_dir:
            return {"ok": False, "error": "Укажи repo_dir (путь к каталогу репозитория)."}
        qdrant_url = get_qdrant_url(self._redis_url)
        if not qdrant_url:
            return {
                "ok": False,
                "error": "Qdrant не настроен. Задай QDRANT_URL в env или дашборде.",
            }
        p = Path(repo_dir)
        if not p.is_absolute():
            workspace = os.getenv("WORKSPACE_DIR", "").strip() or os.getenv(
                "SANDBOX_WORKSPACE_DIR", "/workspace"
            )
            p = Path(workspace) / repo_dir
        chunks, files_count, err = index_repo_to_qdrant(
            p,
            qdrant_url=qdrant_url,
            collection=collection,
            redis_url=self._redis_url,
            user_id=user_id,
        )
        if err:
            return {"ok": False, "error": err}
        return {
            "ok": True,
            "chunks_indexed": chunks,
            "files_count": files_count,
            "repo_dir": str(p),
            "collection": collection,
        }
