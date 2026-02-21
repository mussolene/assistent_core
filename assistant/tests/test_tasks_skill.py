"""Tests for tasks skill: CRUD, isolation by user_id, reminders, format_for_telegram."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.skills.tasks import (
    TaskSkill,
    format_tasks_for_telegram,
    format_tasks_list_readable,
    get_due_reminders_sync,
    _normalize_action,
    _parse_time_spent,
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
async def test_tasks_create_requires_user_id(skill):
    out = await skill.run({"action": "create_task", "title": "X"})
    assert out.get("ok") is False
    assert "user_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_create_requires_title(skill):
    out = await skill.run({"action": "create_task", "user_id": "u1"})
    assert out.get("ok") is False
    assert "title" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_tasks_create_without_dates_keeps_none(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        out = await skill.run({"action": "create_task", "user_id": "u1", "title": "Без дат"})
    assert out.get("ok") is True
    assert out["task"].get("start_date") is None
    assert out["task"].get("end_date") is None


@pytest.mark.asyncio
async def test_tasks_create_drops_past_year_dates(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        out = await skill.run({
            "action": "create_task",
            "user_id": "u1",
            "title": "С датой 2024",
            "start_date": "2024-01-15",
            "end_date": "2024-02-01",
        })
    assert out.get("ok") is True
    assert out["task"].get("start_date") is None
    assert out["task"].get("end_date") is None


@pytest.mark.asyncio
async def test_tasks_create_and_list(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        out = await skill.run({
            "action": "create_task",
            "user_id": "user1",
            "title": "Первая задача",
            "description": "Описание",
            "start_date": "2025-02-20",
            "end_date": "2025-02-25",
        })
    assert out.get("ok") is True
    assert "task_id" in out
    task_id = out["task_id"]
    assert out["task"]["user_id"] == "user1"
    assert out["task"]["title"] == "Первая задача"
    assert out["task"]["start_date"] == "2025-02-20"
    assert out["task"]["end_date"] == "2025-02-25"

    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        list_out = await skill.run({"action": "list_tasks", "user_id": "user1"})
    assert list_out.get("ok") is True
    assert len(list_out.get("tasks", [])) == 1
    assert list_out["tasks"][0]["id"] == task_id
    assert list_out["tasks"][0]["title"] == "Первая задача"


@pytest.mark.asyncio
async def test_tasks_isolated_by_user(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        await skill.run({"action": "create_task", "user_id": "alice", "title": "Alice task"})
        await skill.run({"action": "create_task", "user_id": "bob", "title": "Bob task"})
        list_a = await skill.run({"action": "list_tasks", "user_id": "alice"})
        list_b = await skill.run({"action": "list_tasks", "user_id": "bob"})
    assert list_a.get("ok") is True and list_b.get("ok") is True
    assert len(list_a["tasks"]) == 1 and list_a["tasks"][0]["title"] == "Alice task"
    assert len(list_b["tasks"]) == 1 and list_b["tasks"][0]["title"] == "Bob task"


@pytest.mark.asyncio
async def test_tasks_delete(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "To delete"})
        task_id = cr["task_id"]
        del_out = await skill.run({"action": "delete_task", "user_id": "u1", "task_id": task_id})
    assert del_out.get("ok") is True
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        list_out = await skill.run({"action": "list_tasks", "user_id": "u1"})
    assert list_out.get("tasks", []) == []


@pytest.mark.asyncio
async def test_tasks_update(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "Old"})
        task_id = cr["task_id"]
        await skill.run({"action": "update_task", "user_id": "u1", "task_id": task_id, "title": "New", "status": "done"})
        one = await skill.run({"action": "get_task", "user_id": "u1", "task_id": task_id})
    assert one["task"]["title"] == "New"
    assert one["task"]["status"] == "done"


@pytest.mark.asyncio
async def test_tasks_add_link_and_document(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        cr = await skill.run({"action": "create_task", "user_id": "u1", "title": "T"})
        task_id = cr["task_id"]
        await skill.run({"action": "add_link", "user_id": "u1", "task_id": task_id, "link": {"url": "https://x.com", "name": "X"}})
        await skill.run({"action": "add_document", "user_id": "u1", "task_id": task_id, "document": {"url": "https://doc", "name": "Doc"}})
        one = await skill.run({"action": "get_task", "user_id": "u1", "task_id": task_id})
    assert len(one["task"].get("links", [])) == 1
    assert len(one["task"].get("documents", [])) == 1


@pytest.mark.asyncio
async def test_tasks_search_tasks(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Работа с репозиторием", "description": "Настроить git"})
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Документация по репо", "description": ""})
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Позвонить маме", "description": ""})
        out = await skill.run({"action": "search_tasks", "user_id": "u1", "query": "репо"})
    assert out.get("ok") is True
    assert out.get("total") == 2
    titles = [t["title"] for t in out["tasks"]]
    assert "Работа с репозиторием" in titles
    assert "Документация по репо" in titles
    assert "Позвонить маме" not in titles

    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        empty = await skill.run({"action": "search_tasks", "user_id": "u1", "query": "неттакого"})
    assert empty.get("ok") is True
    assert empty.get("total") == 0


@pytest.mark.asyncio
async def test_tasks_format_for_telegram_with_task_ids(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        cr1 = await skill.run({"action": "create_task", "user_id": "u1", "title": "A"})
        await skill.run({"action": "create_task", "user_id": "u1", "title": "B"})
        out = await skill.run({
            "action": "format_for_telegram",
            "user_id": "u1",
            "task_ids": [cr1["task_id"]],
            "button_action": "delete",
        })
    assert out.get("ok") is True
    assert out.get("tasks_count") == 1
    assert "Удалить" in out["inline_keyboard"][0][0]["text"]
    assert out["inline_keyboard"][0][0]["callback_data"] == f"task:delete:{cr1['task_id']}"


@pytest.mark.asyncio
async def test_tasks_cannot_access_other_user_task(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        cr = await skill.run({"action": "create_task", "user_id": "owner", "title": "Secret"})
        task_id = cr["task_id"]
        get_other = await skill.run({"action": "get_task", "user_id": "other", "task_id": task_id})
        del_other = await skill.run({"action": "delete_task", "user_id": "other", "task_id": task_id})
    assert get_other.get("ok") is False
    assert del_other.get("ok") is False


def test_format_tasks_for_telegram_empty():
    text, kb = format_tasks_for_telegram([])
    assert text == "Нет задач."
    assert kb == []


def test_format_tasks_for_telegram_with_items():
    tasks = [
        {"id": "a1", "title": "Task 1", "start_date": "2025-02-20", "end_date": "2025-02-25", "status": "open"},
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


def test_parse_time_spent():
    assert _parse_time_spent(None) is None
    assert _parse_time_spent(30) == 30
    assert _parse_time_spent("2h") == 120
    assert _parse_time_spent("1.5 часа") == 90
    assert _parse_time_spent("45 min") == 45


def test_format_tasks_list_readable_with_workload_and_time_spent():
    tasks = [
        {"id": "1", "title": "Задача с оценкой", "start_date": "2025-02-20", "end_date": "2025-02-25", "status": "open", "workload": "2ч", "time_spent_minutes": 90},
    ]
    text = format_tasks_list_readable(tasks)
    assert "Задача с оценкой" in text
    assert "оценка: 2ч" in text
    assert "затрачено: 1 ч 30 мин" in text


@pytest.mark.asyncio
async def test_tasks_list_returns_formatted(skill, redis_mock):
    with patch("assistant.skills.tasks._get_redis", new_callable=AsyncMock, return_value=redis_mock):
        await skill.run({"action": "create_task", "user_id": "u1", "title": "Тест"})
        out = await skill.run({"action": "list_tasks", "user_id": "u1"})
    assert out.get("ok") is True
    assert "formatted" in out
    assert "Тест" in out["formatted"]


def test_get_due_reminders_sync_empty():
    with patch("redis.from_url") as from_url:
        client = MagicMock()
        client.zrangebyscore.return_value = []
        client.get.return_value = None
        from_url.return_value = client
        out = get_due_reminders_sync("redis://localhost/0")
    assert out == []
