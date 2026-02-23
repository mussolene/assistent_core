"""Внешние интеграции: Microsoft To-Do, Google Calendar (Фаза 2 дорожной карты 2026)."""

from assistant.integrations.calendar import (
    add_calendar_event,
    calendar_is_configured,
)
from assistant.integrations.todo import (
    create_task_in_todo,
    list_todo_lists,
    todo_is_configured,
)

__all__ = [
    "create_task_in_todo",
    "list_todo_lists",
    "todo_is_configured",
    "add_calendar_event",
    "calendar_is_configured",
]
