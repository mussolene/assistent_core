"""Tests for tasks skill: CRUD, isolation by user_id, reminders, format_for_telegram."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.skills.tasks import (
    TaskSkill,
    format_tasks_for_telegram,
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


def test_get_due_reminders_sync_empty():
    with patch("redis.from_url") as from_url:
        client = MagicMock()
        client.zrangebyscore.return_value = []
        client.get.return_value = None
        from_url.return_value = client
        out = get_due_reminders_sync("redis://localhost/0")
    assert out == []
