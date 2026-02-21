"""Tests for TaskManager with mocked Redis (no real Redis required)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.core.task_manager import KEY_PREFIX, TaskManager


@pytest.mark.asyncio
async def test_create_id():
    tm = TaskManager("redis://localhost:6379/0")
    uid = tm.create_id()
    assert uid
    assert len(uid) == 36  # uuid4 hex + dashes
    assert uid != tm.create_id()


@pytest.mark.asyncio
async def test_key_prefix():
    tm = TaskManager("redis://localhost:6379/0")
    assert tm._key("abc") == f"{KEY_PREFIX}abc"


@pytest.mark.asyncio
async def test_create_get_update_with_mock_redis():
    stored = {}

    async def mock_set(key: str, value: str, ex: int | None = None) -> None:
        stored[key] = value

    async def mock_get(key: str) -> str | None:
        return stored.get(key)

    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.set = AsyncMock(side_effect=mock_set)
    mock_client.get = AsyncMock(side_effect=mock_get)

    with patch("assistant.core.task_manager.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        tm = TaskManager("redis://localhost:6379/0")
        await tm.connect()
        task_id = await tm.create(user_id="u1", chat_id="c1", text="hi")
        assert task_id
        task = await tm.get(task_id)
        assert task is not None
        assert task["user_id"] == "u1"
        assert task["chat_id"] == "c1"
        assert task["text"] == "hi"
        assert task["state"] == "received"
        await tm.update(task_id, state="assistant", iteration=1)
        task2 = await tm.get(task_id)
        assert task2 is not None
        assert task2["state"] == "assistant"
        assert task2["iteration"] == 1


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.get = AsyncMock(return_value=None)

    with patch("assistant.core.task_manager.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        tm = TaskManager("redis://localhost:6379/0")
        await tm.connect()
        out = await tm.get("nonexistent-id")
        assert out is None
