"""Tests for tasks skill: CRUD, isolation by user_id, reminders, format_for_telegram."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.skills.tasks import (
    REDIS_TASK_PREFIX,
    TaskSkill,
    _check_owner,
    _date_to_ordinal,
    _format_task_created_reply,
    _human_date,
    _is_actual_task,
    _normalize_action,
    _normalize_task_params,
    _ordinal_to_date,
    _parse_time_spent,
    _task_matches_query,
    format_task_details,
    format_tasks_for_telegram,
    format_tasks_list_readable,
    get_due_reminders_sync,
)


@pytest.fixture
def redis_mock():
    """Mock Redis: store keys in dict, support get/set/delete/zadd/zrangebyscore/zrem/zremrangebyscore."""
    data = {}
    zsets = {}

    class Client:
        async def ping(self):
            pass

        async def get(self, key):
            return data.get(key)

        async def set(self, key, value, ex=None):
            data[key] = value

        async def delete(self, *keys):
            for k in keys:
                data.pop(k, None)
                zsets.pop(k, None)

        async def zadd(self, key, mapping):
            if key not in zsets:
                zsets[key] = {}
            for member, score in mapping.items():
                zsets[key][member] = score

        async def zrangebyscore(self, key, min_, max_):
            if key not in zsets:
                return []
            return [m for m, s in zsets[key].items() if min_ <= s <= max_]

        async def zrem(self, key, *members):
            if key in zsets:
                for m in members:
                    zsets[key].pop(m, None)

        async def zremrangebyscore(self, key, min_, max_):
            if key in zsets:
                to_remove = [m for m, s in zsets[key].items() if min_ <= s <= max_]
                for m in to_remove:
                    zsets[key].pop(m, None)

        async def aclose(self):
            pass

    client = Client()
    client._data = data
    client._zsets = zsets
    return client


@pytest.fixture
def skill():
    return TaskSkill()


@pytest.mark.asyncio
async def test_tasks_redis_ping_raises_returns_error(skill):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock) as mock_get:
        client = MagicMock()
        client.ping = AsyncMock(side_effect=ConnectionError("redis down"))
        client.aclose = AsyncMock()
        mock_get.return_value = client
        out = await skill.run({"action": "create_task", "user_id": "u1", "title": "X"})
    assert out.get("ok") is False
    assert "Redis" in out.get("error", "") or "redis" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_unknown_action_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run({"action": "unknown_action", "user_id": "u1"})
    assert out.get("ok") is False
    assert "Неизвестное" in out.get("error", "") or "действие" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_get_due_reminders_action(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run({"action": "get_due_reminders", "user_id": "u1"})
    assert out.get("ok") is True
    assert "due_reminders" in out and isinstance(out["due_reminders"], list)


@pytest.mark.asyncio
async def test_tasks_create_requires_user_id(skill):
    out = await skill.run({"action": "create_task", "title": "X"})
    assert out.get("ok") is False
    assert "user_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_create_requires_title(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run({"action": "create_task", "user_id": "u1"})
    assert out.get("ok") is False
    assert "title" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_create_without_dates_keeps_none(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run({"action": "create_task", "user_id": "u1", "title": "Без дат"})
    assert out.get("ok") is True
    assert out["task"].get("start_date") is None
    assert out["task"].get("end_date") is None


@pytest.mark.asyncio
async def test_tasks_create_drops_past_year_dates(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run(
            {
                "action": "create_task",
                "user_id": "u1",
                "title": "С датой 2024",
                "start_date": "2024-01-15",
                "end_date": "2024-02-01",
            }
        )
    assert out.get("ok") is True
    assert out["task"].get("start_date") is None
    assert out["task"].get("end_date") is None


@pytest.mark.asyncio
async def test_tasks_create_and_list(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run(
            {
                "action": "create_task",
                "user_id": "user1",
                "title": "Первая задача",
                "description": "Описание",
                "start_date": "2026-02-20",
                "end_date": "2026-02-25",
            }
        )
    assert out.get("ok") is True
    assert "task_id" in out
    task_id = out["task_id"]
    assert out["task"]["user_id"] == "user1"
    assert out["task"]["title"] == "Первая задача"
    assert out["task"]["start_date"] == "2026-02-20"
    assert out["task"]["end_date"] == "2026-02-25"

    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        list_out = await skill.run({"action": "list_tasks", "user_id": "user1"})
    assert list_out.get("ok") is True
    assert len(list_out.get("tasks", [])) == 1
    assert list_out["tasks"][0]["id"] == task_id
    assert list_out["tasks"][0]["title"] == "Первая задача"


@pytest.mark.asyncio
async def test_tasks_isolated_by_user(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "alice", "title": "Alice task"})
        await skill.run({"action": "create_task", "user_id": "bob", "title": "Bob task"})
        list_a = await skill.run({"action": "list_tasks", "user_id": "alice"})
        list_b = await skill.run({"action": "list_tasks", "user_id": "bob"})
    assert list_a.get("ok") is True and list_b.get("ok") is True
    assert len(list_a["tasks"]) == 1 and list_a["tasks"][0]["title"] == "Alice task"
    assert len(list_b["tasks"]) == 1 and list_b["tasks"][0]["title"] == "Bob task"


def test_tasks_check_owner():
    assert _check_owner({"user_id": "u1"}, "u1") is True
    assert _check_owner({"user_id": "u1"}, "u2") is False
    assert _check_owner(None, "u1") is False
    assert _check_owner({}, "u1") is False


def test_tasks_normalize_action_alias():
    assert _normalize_action("listtasks") == "list_tasks"
    assert _normalize_action("create_task") == "create_task"
    assert _normalize_action("  listtasks  ") == "list_tasks"


@pytest.mark.asyncio
async def test_tasks_list_tasks_action_alias_listtasks(skill, redis_mock):
    """Action 'listtasks' (alias) works like list_tasks."""
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "X"})
        out = await skill.run({"action": "listtasks", "user_id": "u1"})
    assert out.get("ok") is True
    assert len(out.get("tasks", [])) == 1


@pytest.mark.asyncio
async def test_tasks_get_task_wrong_user_returns_error(skill, redis_mock):
    """get_task with task_id belonging to another user returns not ok."""
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "alice", "title": "Secret"})
        task_id = cr["task_id"]
        out = await skill.run({"action": "get_task", "user_id": "bob", "task_id": task_id})
    assert out.get("ok") is False or "task" not in out or out.get("task") is None


@pytest.mark.asyncio
async def test_tasks_delete_task_corrupt_json_returns_error(skill, redis_mock):
    """delete_task when task key has invalid JSON -> _load_task returns None -> error or no-op."""
    redis_mock._data[f"{REDIS_TASK_PREFIX}fake-id"] = "{invalid json"
    redis_mock._data["assistant:tasks:user:u1"] = json.dumps(["fake-id"])
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run({"action": "delete_task", "user_id": "u1", "task_id": "fake-id"})
    assert out.get("ok") is False or "error" in out


@pytest.mark.asyncio
async def test_tasks_delete(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "To delete"})
        task_id = cr["task_id"]
        del_out = await skill.run({"action": "delete_task", "user_id": "u1", "task_id": task_id})
    assert del_out.get("ok") is True
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        list_out = await skill.run({"action": "list_tasks", "user_id": "u1"})
    assert list_out.get("tasks", []) == []


@pytest.mark.asyncio
async def test_tasks_update_wrong_user_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "alice", "title": "T"})
        out = await skill.run(
            {"action": "update_task", "user_id": "bob", "task_id": cr["task_id"], "title": "Hack"}
        )
    assert out.get("ok") is False


@pytest.mark.asyncio
async def test_tasks_list_with_status_filter(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Open task"})
        cr2 = await skill.run({"action": "create_task", "user_id": "u1", "title": "Done task"})
        await skill.run(
            {"action": "update_task", "user_id": "u1", "task_id": cr2["task_id"], "status": "done"}
        )
        out = await skill.run({"action": "list_tasks", "user_id": "u1", "status": "done"})
    assert out.get("ok") is True
    assert len(out.get("tasks", [])) == 1
    assert out["tasks"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_tasks_update(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "Old"})
        task_id = cr["task_id"]
        await skill.run(
            {
                "action": "update_task",
                "user_id": "u1",
                "task_id": task_id,
                "title": "New",
                "status": "done",
            }
        )
        one = await skill.run({"action": "get_task", "user_id": "u1", "task_id": task_id})
    assert one["task"]["title"] == "New"
    assert one["task"]["status"] == "done"


@pytest.mark.asyncio
async def test_tasks_set_reminder_invalid_datetime_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "T"})
        task_id = cr["task_id"]
        out = await skill.run(
            {
                "action": "set_reminder",
                "user_id": "u1",
                "task_id": task_id,
                "reminder_at": "not-a-date",
            }
        )
    assert out.get("ok") is False
    assert "reminder_at" in out.get("error", "") or "ISO" in out.get("error", "")


@pytest.mark.asyncio
async def test_tasks_add_link_missing_link_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "T"})
        out = await skill.run({"action": "add_link", "user_id": "u1", "task_id": cr["task_id"]})
    assert out.get("ok") is False
    assert "link" in out.get("error", "").lower() or "task_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_add_link_and_document(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "T"})
        task_id = cr["task_id"]
        await skill.run(
            {
                "action": "add_link",
                "user_id": "u1",
                "task_id": task_id,
                "link": {"url": "https://x.com", "name": "X"},
            }
        )
        await skill.run(
            {
                "action": "add_document",
                "user_id": "u1",
                "task_id": task_id,
                "document": {"url": "https://doc", "name": "Doc"},
            }
        )
        one = await skill.run({"action": "get_task", "user_id": "u1", "task_id": task_id})
    assert len(one["task"].get("links", [])) == 1
    assert len(one["task"].get("documents", [])) == 1


@pytest.mark.asyncio
async def test_tasks_add_document_no_task_id_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        out = await skill.run(
            {
                "action": "add_document",
                "user_id": "u1",
                "document": {"name": "d", "url": "http://d"},
            }
        )
    assert out.get("ok") is False
    assert "task_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_add_document_no_document_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "T"})
        out = await skill.run({"action": "add_document", "user_id": "u1", "task_id": cr["task_id"]})
    assert out.get("ok") is False
    assert "document" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_add_link_wrong_user_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "alice", "title": "T"})
        out = await skill.run(
            {
                "action": "add_link",
                "user_id": "bob",
                "task_id": cr["task_id"],
                "link": {"url": "https://x.com", "name": "X"},
            }
        )
    assert out.get("ok") is False
    assert "доступ запрещён" in out.get("error", "") or "не найдена" in out.get("error", "")


@pytest.mark.asyncio
async def test_tasks_add_document_wrong_user_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "alice", "title": "T"})
        out = await skill.run(
            {
                "action": "add_document",
                "user_id": "bob",
                "task_id": cr["task_id"],
                "document": {"url": "https://d", "name": "D"},
            }
        )
    assert out.get("ok") is False
    assert "доступ запрещён" in out.get("error", "") or "не найдена" in out.get("error", "")


@pytest.mark.asyncio
async def test_tasks_search_tasks(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run(
            {
                "action": "create_task",
                "user_id": "u1",
                "title": "Работа с репозиторием",
                "description": "Настроить git",
            }
        )
        await skill.run(
            {
                "action": "create_task",
                "user_id": "u1",
                "title": "Документация по репо",
                "description": "",
            }
        )
        await skill.run(
            {"action": "create_task", "user_id": "u1", "title": "Позвонить маме", "description": ""}
        )
        out = await skill.run({"action": "search_tasks", "user_id": "u1", "query": "репо"})
    assert out.get("ok") is True
    assert out.get("total") == 2
    titles = [t["title"] for t in out["tasks"]]
    assert "Работа с репозиторием" in titles
    assert "Документация по репо" in titles
    assert "Позвонить маме" not in titles

    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        empty = await skill.run({"action": "search_tasks", "user_id": "u1", "query": "неттакого"})
    assert empty.get("ok") is True
    assert empty.get("total") == 0


@pytest.mark.asyncio
async def test_tasks_format_for_telegram_with_task_ids(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr1 = await skill.run({"action": "create_task", "user_id": "u1", "title": "A"})
        await skill.run({"action": "create_task", "user_id": "u1", "title": "B"})
        out = await skill.run(
            {
                "action": "format_for_telegram",
                "user_id": "u1",
                "task_ids": [cr1["task_id"]],
                "button_action": "delete",
            }
        )
    assert out.get("ok") is True
    assert out.get("tasks_count") == 1
    assert "Удалить" in out["inline_keyboard"][0][0]["text"]
    assert out["inline_keyboard"][0][0]["callback_data"] == f"task:delete:{cr1['task_id']}"


@pytest.mark.asyncio
async def test_tasks_format_for_telegram_show_done_button(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "T"})
        out = await skill.run(
            {
                "action": "format_for_telegram",
                "user_id": "u1",
                "show_done_button": True,
            }
        )
    assert out.get("ok") is True
    assert "inline_keyboard" in out
    assert out.get("tasks_count") >= 0


@pytest.mark.asyncio
async def test_tasks_set_reminder_wrong_user_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "alice", "title": "T"})
        out = await skill.run(
            {
                "action": "set_reminder",
                "user_id": "bob",
                "task_id": cr["task_id"],
                "reminder_at": "2025-12-31T12:00:00Z",
            }
        )
    assert out.get("ok") is False
    assert "доступ запрещён" in out.get("error", "") or "не найдена" in out.get("error", "")


@pytest.mark.asyncio
async def test_tasks_cannot_access_other_user_task(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "owner", "title": "Secret"})
        task_id = cr["task_id"]
        get_other = await skill.run({"action": "get_task", "user_id": "other", "task_id": task_id})
        del_other = await skill.run(
            {"action": "delete_task", "user_id": "other", "task_id": task_id}
        )
    assert get_other.get("ok") is False
    assert del_other.get("ok") is False


@pytest.mark.asyncio
async def test_tasks_archive_completed(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Open"})
        cr2 = await skill.run({"action": "create_task", "user_id": "u1", "title": "Done"})
        await skill.run(
            {"action": "update_task", "user_id": "u1", "task_id": cr2["task_id"], "status": "done"}
        )
        out = await skill.run({"action": "archive_completed", "user_id": "u1"})
        assert out.get("ok") is True
        assert out.get("archived_count") == 1
        list_out = await skill.run({"action": "list_tasks", "user_id": "u1"})
        assert list_out.get("total") == 1
        assert all(t.get("status") != "done" for t in list_out["tasks"])
        archive_out = await skill.run({"action": "list_archive", "user_id": "u1"})
    assert archive_out.get("ok") is True
    assert archive_out.get("total") == 1
    assert archive_out["tasks"][0]["title"] == "Done"


@pytest.mark.asyncio
async def test_tasks_list_archive_with_date_filter(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "T1"})
        cr2 = await skill.run({"action": "create_task", "user_id": "u1", "title": "T2"})
        await skill.run(
            {"action": "update_task", "user_id": "u1", "task_id": cr2["task_id"], "status": "done"}
        )
        await skill.run({"action": "archive_completed", "user_id": "u1"})
        out = await skill.run(
            {"action": "list_archive", "user_id": "u1", "from_date": "2020-01-01", "to_date": "2030-12-31"}
        )
    assert out.get("ok") is True
    assert out.get("total") >= 1
    assert "formatted" in out


@pytest.mark.asyncio
async def test_tasks_search_archive(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr1 = await skill.run({"action": "create_task", "user_id": "u1", "title": "Report Q1"})
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Meeting notes"})
        await skill.run(
            {"action": "update_task", "user_id": "u1", "task_id": cr1["task_id"], "status": "done"}
        )
        await skill.run({"action": "archive_completed", "user_id": "u1"})
        out = await skill.run(
            {"action": "search_archive", "user_id": "u1", "query": "Report"}
        )
    assert out.get("ok") is True
    assert out.get("total") == 1
    assert out["tasks"][0]["title"] == "Report Q1"
    assert "formatted" in out


@pytest.mark.asyncio
async def test_tasks_search_archive_with_date_filter(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr1 = await skill.run({"action": "create_task", "user_id": "u1", "title": "Done task"})
        await skill.run(
            {"action": "update_task", "user_id": "u1", "task_id": cr1["task_id"], "status": "done"}
        )
        await skill.run({"action": "archive_completed", "user_id": "u1"})
        out = await skill.run(
            {
                "action": "search_archive",
                "user_id": "u1",
                "from_date": "2020-01-01",
                "to_date": "2030-12-31",
            }
        )
    assert out.get("ok") is True
    assert out.get("total") >= 1
    assert "formatted" in out


@pytest.mark.asyncio
async def test_tasks_subtasks_create_and_list(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        parent = await skill.run({"action": "create_task", "user_id": "u1", "title": "Parent"})
        sub1 = await skill.run(
            {"action": "create_task", "user_id": "u1", "title": "Sub 1", "parent_id": parent["task_id"]}
        )
        await skill.run(
            {"action": "create_task", "user_id": "u1", "title": "Sub 2", "parent_id": parent["task_id"]}
        )
        out = await skill.run({"action": "list_subtasks", "user_id": "u1", "parent_id": parent["task_id"]})
        assert out.get("ok") is True
        assert out.get("total") == 2
        assert all(t.get("parent_id") == parent["task_id"] for t in out["tasks"])
        one = await skill.run({"action": "get_task", "user_id": "u1", "task_id": parent["task_id"]})
    assert one.get("ok") is True
    assert len(one.get("subtasks", [])) == 2
    assert "Подзадачи" in one.get("formatted_details", "")


@pytest.mark.asyncio
async def test_tasks_create_subtask_wrong_parent_returns_error(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "P"})
        out = await skill.run(
            {"action": "create_task", "user_id": "other", "title": "Sub", "parent_id": cr["task_id"]}
        )
    assert out.get("ok") is False
    assert "Родительская" in out.get("error", "") or "доступ" in out.get("error", "")


def test_format_tasks_for_telegram_empty():
    text, kb = format_tasks_for_telegram([])
    assert text == "Нет задач."
    assert kb == []


def test_format_tasks_for_telegram_with_items():
    tasks = [
        {
            "id": "a1",
            "title": "Task 1",
            "start_date": "2025-02-20",
            "end_date": "2025-02-25",
            "status": "open",
        },
        {"id": "a2", "title": "Task 2", "start_date": None, "end_date": None, "status": "done"},
    ]
    text, kb = format_tasks_for_telegram(tasks)
    assert "Task 1" in text and "Task 2" in text
    assert len(kb) == 2
    assert kb[0][0]["callback_data"] == "task:view:a1"


def test_format_tasks_for_telegram_action_delete():
    tasks = [{"id": "x1", "title": "По работе с репозиторием", "status": "open"}]
    text, kb = format_tasks_for_telegram(tasks, action="delete")
    assert "Удалить" in kb[0][0]["text"]
    assert kb[0][0]["callback_data"] == "task:delete:x1"


def test_normalize_action():
    assert _normalize_action("list_tasks") == "list_tasks"
    assert _normalize_action("listtasks") == "list_tasks"
    assert _normalize_action("create_task") == "create_task"
    assert _normalize_action("createtask") == "create_task"


def test_format_task_created_reply():
    t = {
        "title": "Решение проблемы с репозиториями",
        "start_date": "2026-02-22",
        "end_date": "2026-03-10",
        "workload": "2 дня",
    }
    s = _format_task_created_reply(t)
    assert "Задача создана" in s
    assert "Решение проблемы" in s
    assert "22.02" in s and "10.03" in s
    assert "Оценка: 2 дня" in s
    t2 = {"title": "Без дат"}
    assert "Срок:" not in _format_task_created_reply(t2)


def test_task_matches_query():
    assert _task_matches_query({"title": "A", "description": "B"}, "") is True
    assert _task_matches_query({"title": "Hello", "description": "World"}, "hello") is True
    assert _task_matches_query({"title": "X", "description": "Secret word"}, "word") is True
    assert _task_matches_query({"title": "X", "description": "Y"}, "z") is False


def test_human_date():
    assert _human_date("2026-02-20") == "20.02"
    assert _human_date(None) == ""
    assert _human_date("") == ""
    assert _human_date("2026-02-20T12:00:00") == "20.02"


def test_ordinal_to_date():
    from datetime import date

    d = date(2026, 2, 20)
    assert _ordinal_to_date(d.toordinal()) == "2026-02-20"


def test_is_actual_task():
    from datetime import date

    today = date.today().isoformat()
    assert _is_actual_task({"status": "open", "end_date": None}) is True
    assert _is_actual_task({"status": "open", "end_date": today}) is True
    assert _is_actual_task({"status": "done"}) is False
    assert _is_actual_task({"status": "open", "end_date": "2020-01-01"}) is False


@pytest.mark.asyncio
async def test_tasks_list_only_actual(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run(
            {
                "action": "create_task",
                "user_id": "u1",
                "title": "Open",
                "status": "open",
                "end_date": "2030-01-01",
            }
        )
        await skill.run(
            {"action": "create_task", "user_id": "u1", "title": "Done", "status": "done"}
        )
        out = await skill.run({"action": "list_tasks", "user_id": "u1", "only_actual": True})
    assert out.get("ok") is True
    assert out.get("total") == 1
    assert any(t["title"] == "Open" for t in out["tasks"])
    assert "inline_keyboard" in out
    assert "text_telegram" in out
    assert "Выполнена" in str(out["inline_keyboard"]) or len(out["inline_keyboard"]) >= 1


def test_normalize_task_params():
    assert _normalize_task_params({"startdate": "2026-02-22", "enddate": "2026-03-10"}) == {
        "start_date": "2026-02-22",
        "end_date": "2026-03-10",
    }
    assert (
        _normalize_task_params({"title": "X", "start_date": "2025-01-01"})["start_date"]
        == "2025-01-01"
    )


def test_date_to_ordinal():
    assert _date_to_ordinal("2026-02-20") is not None
    assert _date_to_ordinal(None) is None
    assert _date_to_ordinal("") is None
    assert _date_to_ordinal("bad-date") is None


def test_parse_time_spent():
    assert _parse_time_spent(None) is None
    assert _parse_time_spent(30) == 30
    assert _parse_time_spent(1.5) == 90
    assert _parse_time_spent("2h") == 120
    assert _parse_time_spent("1.5 часа") == 90
    assert _parse_time_spent("45 min") == 45


def test_format_tasks_list_readable_title_and_created():
    tasks = [
        {"id": "1", "title": "Задача", "created_at": "2025-02-20T12:00:00", "status": "open"},
    ]
    text = format_tasks_list_readable(tasks)
    assert "Задача" in text
    assert "создана" in text and "20.02" in text


def test_format_tasks_list_readable_with_workload_and_time_spent():
    tasks = [
        {
            "id": "1",
            "title": "Задача с оценкой",
            "created_at": "2025-02-20T12:00:00",
            "status": "open",
            "workload": "2ч",
            "time_spent_minutes": 90,
        },
    ]
    text = format_tasks_list_readable(tasks, include_workload=True, include_time_spent=True)
    assert "Задача с оценкой" in text
    assert "оценка" in text and "2ч" in text
    assert "затрачено" in text


@pytest.mark.asyncio
async def test_tasks_list_returns_formatted(skill, redis_mock):
    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Тест"})
        out = await skill.run({"action": "list_tasks", "user_id": "u1"})
    assert out.get("ok") is True
    assert "formatted" in out
    assert "Тест" in out["formatted"]


def test_format_task_details():
    t = {
        "title": "Тест",
        "description": "Описание",
        "created_at": "2025-02-20T12:00:00",
        "status": "open",
        "documents": [{"name": "Doc", "url": "https://x.com"}],
    }
    s = format_task_details(t)
    assert "Тест" in s and "Описание" in s and "Документы" in s and "Doc" in s


def test_get_due_reminders_sync_empty():
    with patch("redis.from_url") as from_url:
        client = MagicMock()
        client.zrangebyscore.return_value = []
        client.get.return_value = None
        from_url.return_value = client
        out = get_due_reminders_sync("redis://localhost/0")
    assert out == []


def test_get_due_reminders_sync_redis_raises_returns_empty():
    with patch("redis.from_url", side_effect=ConnectionError("redis down")):
        out = get_due_reminders_sync("redis://localhost/0")
    assert out == []


def test_get_due_reminders_sync_invalid_json_skips_task():
    with patch("redis.from_url") as from_url:
        client = MagicMock()
        client.zrangebyscore.return_value = ["tid1"]
        client.zremrangebyscore = MagicMock()
        client.get.return_value = "{invalid"
        client.close = MagicMock()
        from_url.return_value = client
        out = get_due_reminders_sync("redis://localhost/0")
    assert out == []


@pytest.mark.asyncio
async def test_tasks_set_reminder_naive_datetime_treated_as_utc(skill, redis_mock):
    """reminder_at без таймзоны (2025-12-31T15:00:00) сохраняется как UTC (+00:00)."""
    from assistant.skills.tasks import REDIS_TASK_PREFIX

    with patch(
        "assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock
    ):
        cr = await skill.run({"action": "create_task", "title": "Напомнить", "user_id": "u1"})
        assert cr["ok"] is True
        task_id = cr["task_id"]
        out = await skill.run(
            {
                "action": "set_reminder",
                "task_id": task_id,
                "user_id": "u1",
                "reminder_at": "2025-12-31T15:00:00",
            }
        )
        assert out["ok"] is True
        assert "reminder_at" in out
        assert "+00:00" in out["reminder_at"] or out["reminder_at"].endswith("Z")
        raw = await redis_mock.get(f"{REDIS_TASK_PREFIX}{task_id}")
        task = json.loads(raw)
        assert "+00:00" in task["reminder_at"] or task["reminder_at"].endswith("Z")
