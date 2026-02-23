"""Тесты модуля интеграций (To-Do, Calendar) и скилла integrations."""

import pytest

from assistant.integrations.calendar import add_calendar_event, calendar_is_configured
from assistant.integrations.todo import (
    create_task_in_todo,
    list_todo_lists,
    todo_is_configured,
)


def test_todo_is_configured_without_env():
    """Без MS_TODO_CLIENT_ID интеграция не настроена."""
    assert todo_is_configured() is False


def test_list_todo_lists_not_configured():
    """Без настройки list_todo_lists возвращает ошибку."""
    out = list_todo_lists()
    assert out.get("ok") is False
    assert "не подключен" in (out.get("error") or "").lower() or "не подключен" in str(out).lower()


def test_create_task_in_todo_empty_title():
    """Пустой title — ошибка."""
    out = create_task_in_todo("")
    assert out.get("ok") is False
    assert "title" in (out.get("error") or "").lower()


def test_create_task_in_todo_not_configured():
    """Без настройки create_task_in_todo возвращает ошибку."""
    out = create_task_in_todo("test task")
    assert out.get("ok") is False


def test_calendar_is_configured():
    """Google Calendar пока не реализован."""
    assert calendar_is_configured() is False


def test_add_calendar_event_empty_title():
    out = add_calendar_event("")
    assert out.get("ok") is False


def test_add_calendar_event_stub():
    out = add_calendar_event("Meeting")
    assert out.get("ok") is False
    assert "Calendar" in (out.get("error") or "")


@pytest.mark.asyncio
async def test_integrations_skill_sync_to_todo():
    """Скилл integrations: sync_to_todo без настройки возвращает ошибку."""
    from assistant.skills.integrations_skill import IntegrationsSkill

    skill = IntegrationsSkill()
    result = await skill.run({"action": "sync_to_todo", "title": "test"})
    assert result.get("ok") is False


@pytest.mark.asyncio
async def test_integrations_skill_add_calendar_event():
    """Скилл integrations: add_calendar_event возвращает заглушку."""
    from assistant.skills.integrations_skill import IntegrationsSkill

    skill = IntegrationsSkill()
    result = await skill.run({"action": "add_calendar_event", "title": "Meeting"})
    assert result.get("ok") is False


@pytest.mark.asyncio
async def test_integrations_skill_list_todo_lists():
    """Скилл integrations: list_todo_lists без настройки."""
    from assistant.skills.integrations_skill import IntegrationsSkill

    skill = IntegrationsSkill()
    result = await skill.run({"action": "list_todo_lists"})
    assert result.get("ok") is False


@pytest.mark.asyncio
async def test_integrations_skill_unknown_action():
    skill = __import__("assistant.skills.integrations_skill", fromlist=["IntegrationsSkill"]).IntegrationsSkill()
    result = await skill.run({"action": "unknown"})
    assert result.get("ok") is False
    assert "Неизвестное" in (result.get("error") or "")
