"""Скилл интеграций: синхронизация с Microsoft To-Do, добавление в Google Calendar (Фаза 2)."""

from __future__ import annotations

import logging
from typing import Any

from assistant.integrations.calendar import add_calendar_event as calendar_add_event
from assistant.integrations.todo import create_task_in_todo, list_todo_lists
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class IntegrationsSkill(BaseSkill):
    """Действия с внешними сервисами: To-Do, Calendar."""

    @property
    def name(self) -> str:
        return "integrations"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or "").strip().lower()
        if not action:
            return {"ok": False, "error": "Укажите action: sync_to_todo или add_calendar_event."}
        if action == "sync_to_todo":
            return await self._sync_to_todo(params)
        if action == "add_calendar_event":
            return self._add_calendar_event(params)
        if action == "list_todo_lists":
            return list_todo_lists()
        return {"ok": False, "error": f"Неизвестное действие: {action}"}

    async def _sync_to_todo(self, params: dict[str, Any]) -> dict[str, Any]:
        """Создать задачу в Microsoft To-Do. Параметры: title, list_id (опционально)."""
        title = (params.get("title") or params.get("text") or "").strip()
        list_id = (params.get("list_id") or "").strip() or None
        result = create_task_in_todo(title=title, list_id=list_id)
        if result.get("ok"):
            result["user_reply"] = f"Задача «{result.get('title', title)}» добавлена в Microsoft To-Do."
        return result

    def _add_calendar_event(self, params: dict[str, Any]) -> dict[str, Any]:
        """Добавить событие в календарь. Параметры: title, start_iso?, end_iso?, description?."""
        title = (params.get("title") or "").strip()
        start_iso = (params.get("start_iso") or params.get("start") or "").strip() or None
        end_iso = (params.get("end_iso") or params.get("end") or "").strip() or None
        description = (params.get("description") or "").strip() or None
        return calendar_add_event(
            title=title,
            start_iso=start_iso,
            end_iso=end_iso,
            description=description,
        )
