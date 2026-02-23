"""Google Calendar: заглушка для Фазы 2. OAuth и создание события — в следующих итерациях."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def calendar_is_configured() -> bool:
    """Проверка: настроена ли интеграция Google Calendar (токены в Redis)."""
    return False


def add_calendar_event(
    title: str,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Добавить событие в Google Calendar. Пока возвращает подсказку по настройке."""
    if not title or not str(title).strip():
        return {"ok": False, "error": "Укажите title события."}
    return {
        "ok": False,
        "error": "Google Calendar пока не подключен. Интеграция запланирована в дорожной карте (Фаза 2). Настройка OAuth и API — в следующих релизах.",
    }
