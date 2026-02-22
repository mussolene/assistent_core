"""Skill: индексация документа в Qdrant — путь → чанки + embedding → upsert (итерация 3.2)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from assistant.core.qdrant_docs import (
    get_qdrant_url,
    index_document_to_qdrant,
)
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class DocumentIndexSkill(BaseSkill):
    """Индексация файла в Qdrant: извлечение текста, чанки, эмбеддинг, upsert."""

    def __init__(self, redis_url: str = "") -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")

    @property
    def name(self) -> str:
        return "index_document"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        path = (params.get("path") or params.get("file_path") or params.get("file") or "").strip()
        user_id = (params.get("user_id") or params.get("user") or "default").strip()
        collection = (params.get("collection") or "documents").strip() or "documents"
        if not path:
            return {"ok": False, "error": "Укажи path (путь к файлу)."}
        qdrant_url = get_qdrant_url(self._redis_url)
        if not qdrant_url:
            return {"ok": False, "error": "Qdrant не настроен. Задай QDRANT_URL в env или дашборде."}
        # path может быть абсолютным или относительным к workspace
        p = Path(path)
        if not p.is_absolute():
            workspace = os.getenv("WORKSPACE_DIR", "").strip() or os.getenv("SANDBOX_WORKSPACE_DIR", "/workspace")
            p = Path(workspace) / path
        mime_type = (params.get("mime_type") or "").strip()
        filename = params.get("filename") or p.name
        count, err = index_document_to_qdrant(
            p,
            user_id=user_id,
            qdrant_url=qdrant_url,
            collection=collection,
            redis_url=self._redis_url,
            mime_type=mime_type,
            filename=filename,
        )
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "chunks_indexed": count, "path": str(p), "collection": collection}
