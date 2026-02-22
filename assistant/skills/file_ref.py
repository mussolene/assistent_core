"""Skill: список и отправка файлов по сохранённым ссылкам (файлы индексированы в вектор, сами файлы не храним — только file_id для Telegram)."""

from __future__ import annotations

import logging
from typing import Any

from assistant.core.file_indexing import get_file_ref, list_file_refs
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class FileRefSkill(BaseSkill):
    """Доступ к сохранённым ссылкам на файлы: list (список по user_id), get/send (вернуть file_id для отправки в чат)."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    @property
    def name(self) -> str:
        return "file_ref"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        user_id = (params.get("user_id") or params.get("user") or "default").strip()
        action = (params.get("action") or "list").lower()
        file_ref_id = (params.get("file_ref_id") or params.get("ref_id") or "").strip()

        if action == "list":
            refs = list_file_refs(self._redis_url, user_id)
            return {
                "ok": True,
                "files": refs,
                "message": f"Сохранённые файлы: {len(refs)}. Для отправки вызови send с file_ref_id.",
            }
        if action in ("get", "send") and file_ref_id:
            ref = get_file_ref(self._redis_url, file_ref_id)
            if not ref:
                return {"ok": False, "error": "Файл не найден по file_ref_id."}
            file_id = ref.get("file_id")
            if not file_id:
                return {"ok": False, "error": "Нет file_id для отправки."}
            # Оркестратор подхватит send_document и добавит в OutgoingReply
            return {
                "ok": True,
                "filename": ref.get("filename") or file_ref_id,
                "send_document": {"file_id": file_id},
            }
        return {"ok": False, "error": "Укажи action (list | send) и при send — file_ref_id."}
