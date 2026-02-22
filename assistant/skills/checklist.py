"""Skill: создание чеклиста для Telegram (send_checklist или текстовый fallback)."""

from __future__ import annotations

import logging
from typing import Any

from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ChecklistSkill(BaseSkill):
    """Чеклист для чата: create — возвращает send_checklist для оркестратора/адаптера."""

    @property
    def name(self) -> str:
        return "checklist"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or "create").lower()
        if action != "create":
            return {"ok": False, "error": "Доступно только action: create (title, tasks)."}

        title = (params.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "Укажи title чеклиста (1–255 символов)."}

        raw_tasks = params.get("tasks")
        if not isinstance(raw_tasks, list):
            return {
                "ok": False,
                "error": "Укажи tasks — массив объектов с полем text (и опционально id).",
            }

        tasks: list[dict[str, Any]] = []
        for i, t in enumerate(raw_tasks[:30]):
            if isinstance(t, dict):
                text = (t.get("text") or "").strip() or "?"
                task_id = t.get("id")
                if task_id is None:
                    task_id = i + 1
                tasks.append({"id": task_id, "text": text[:100]})
            elif isinstance(t, str):
                tasks.append({"id": i + 1, "text": (t.strip() or "?")[:100]})

        if not tasks:
            return {"ok": False, "error": "Нужна хотя бы одна задача в tasks."}

        out: dict[str, Any] = {
            "title": title[:255],
            "tasks": tasks,
        }
        if "others_can_add_tasks" in params:
            out["others_can_add_tasks"] = bool(params["others_can_add_tasks"])
        if "others_can_mark_tasks_as_done" in params:
            out["others_can_mark_tasks_as_done"] = bool(params["others_can_mark_tasks_as_done"])

        return {
            "ok": True,
            "message": f"Чеклист «{title}» с {len(tasks)} пунктами.",
            "send_checklist": out,
        }
