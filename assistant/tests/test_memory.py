"""Tests for memory: short-term, task, summary, manager (Redis or mocks)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.memory.manager import (
    VECTOR_LEVEL_LONG,
    VECTOR_LEVEL_MEDIUM,
    VECTOR_LEVEL_SHORT,
    MemoryManager,
)
from assistant.memory.short_term import ShortTermMemory
from assistant.memory.summary import SummaryMemory
from assistant.memory.task_memory import TaskMemory


@pytest.mark.asyncio
async def test_short_term_memory_in_memory():
    """Test short-term without Redis by using a fake URL and catching connection error or using fakeredis."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/15")
        await r.ping()
    except Exception:
        pytest.skip("Redis not available")
    memory = ShortTermMemory("redis://localhost:6379/15", window=3)
    await memory.connect()
    await memory.append("user1", "user", "hello")
    await memory.append("user1", "assistant", "hi")
    msgs = await memory.get_messages("user1")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "hello"
    await memory.clear("user1")
    assert len(await memory.get_messages("user1")) == 0


@pytest.mark.asyncio
async def test_task_memory():
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/15")
        await r.ping()
    except Exception:
        pytest.skip("Redis not available")
    tm = TaskMemory("redis://localhost:6379/15")
    await tm.connect()
    task_id = "test-task-123"
    await tm.set(task_id, "key1", {"a": 1})
    assert await tm.get(task_id, "key1") == {"a": 1}
    await tm.append_tool_result(task_id, "filesystem", {"ok": True})
    results = await tm.get_tool_results(task_id)
    assert len(results) == 1
    assert results[0]["tool"] == "filesystem"


@pytest.mark.asyncio
async def test_summary_memory_roundtrip():
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/15")
        await r.ping()
    except Exception:
        pytest.skip("Redis not available")
    sm = SummaryMemory("redis://localhost:6379/15")
    await sm.connect()
    await sm.set_summary("user1", "Previous conversation summary.")
    out = await sm.get_summary("user1")
    assert out == "Previous conversation summary."
    assert await sm.get_summary("other_user") is None


@pytest.mark.asyncio
async def test_memory_manager_get_context_no_vector():
    """get_context_for_user with mocked backends and no vector model."""
    mgr = MemoryManager("redis://localhost:6379/0")
    mgr._short = MagicMock()
    mgr._short.get_messages = AsyncMock(
        return_value=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    mgr._summary = MagicMock()
    mgr._summary.get_summary = AsyncMock(return_value="Old summary.")
    mock_vec = MagicMock()
    mock_vec._get_model = MagicMock(return_value=None)
    mgr._get_vector_memory = MagicMock(return_value=mock_vec)
    mgr._user_data = MagicMock()
    mgr._user_data.get = AsyncMock(return_value={})
    mgr._task = MagicMock()
    mgr._task.get_tool_results = AsyncMock(return_value=[])
    ctx = await mgr.get_context_for_user("u1", "task1", include_vector=True)
    assert any("Old summary" in str(m.get("content", "")) for m in ctx)
    assert any(m.get("content") == "hi" for m in ctx)


@pytest.mark.asyncio
async def test_memory_manager_get_context_with_vector_and_tool_results():
    """get_context_for_user with vector model and tool_results (per-user vectors)."""
    mgr = MemoryManager("redis://localhost:6379/0")
    mgr._short = MagicMock()
    mgr._short.get_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    mgr._summary = MagicMock()
    mgr._summary.get_summary = AsyncMock(return_value=None)
    mock_vec_with_hits = MagicMock()
    mock_vec_with_hits._get_model = MagicMock(return_value=MagicMock())
    mock_vec_with_hits.search = MagicMock(return_value=[{"text": "relevant memory", "score": 0.9}])
    mock_vec_empty = MagicMock()
    mock_vec_empty._get_model = MagicMock(return_value=None)

    def get_vec(user_id, level):
        return mock_vec_with_hits if level == "short" else mock_vec_empty

    mgr._get_vector_memory = MagicMock(side_effect=get_vec)
    mgr._user_data = MagicMock()
    mgr._user_data.get = AsyncMock(return_value={})
    mgr._task = MagicMock()
    mgr._task.get_tool_results = AsyncMock(return_value=[{"result": "file content"}])
    ctx = await mgr.get_context_for_user("u1", "task1", include_vector=True)
    assert any("relevant memory" in str(m.get("content", "")) for m in ctx)


@pytest.mark.asyncio
async def test_memory_manager_append_store_append_tool_add_vector():
    """append_message, store_task_fact, append_tool_result, add_to_vector (per user_id)."""
    mgr = MemoryManager("redis://localhost:6379/0")
    mgr._short = MagicMock()
    mgr._short.append = AsyncMock()
    mgr._task = MagicMock()
    mgr._task.set = AsyncMock()
    mgr._task.append_tool_result = AsyncMock()
    mock_vec = MagicMock()
    mock_vec.add = MagicMock()
    mgr._get_vector_memory = MagicMock(return_value=mock_vec)
    await mgr.append_message("u1", "user", "hello")
    mgr._short.append.assert_called_once_with("u1", "user", "hello", "default")
    await mgr.store_task_fact("t1", "key", "value")
    mgr._task.set.assert_called_once_with("t1", "key", "value")
    await mgr.append_tool_result("t1", "fs", {"path": "/x"})
    mgr._task.append_tool_result.assert_called_once_with("t1", "fs", {"path": "/x"})
    await mgr.add_to_vector("u1", "some text", {"source": "test"})
    assert mgr._get_vector_memory.call_count == 3
    assert mock_vec.add.call_count == 3
    args, kwargs = mock_vec.add.call_args
    meta = args[1] if len(args) > 1 else kwargs
    assert "level" in (meta or {})


@pytest.mark.asyncio
async def test_short_term_with_mock_redis():
    """ShortTermMemory get_messages skips bad json; clear calls delete."""
    import json as _json

    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.rpush = AsyncMock()
    mock_client.lrange = AsyncMock(
        return_value=[
            _json.dumps({"role": "user", "content": "ok"}),
            "not-valid-json",
        ]
    )
    mock_client.delete = AsyncMock()
    mock_client.pipeline = MagicMock(
        return_value=MagicMock(
            rpush=MagicMock(),
            ltrim=MagicMock(),
            expire=MagicMock(),
            execute=AsyncMock(),
        )
    )

    with patch("assistant.memory.short_term.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        mem = ShortTermMemory("redis://fake:6379/0", window=5)
        await mem.connect()
        await mem.append("u1", "user", "hello")
        mock_client.pipeline.return_value.rpush.assert_called()
        msgs = await mem.get_messages("u1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "ok"
        await mem.clear("u1")
        mock_client.delete.assert_called_once()


@pytest.mark.asyncio
async def test_memory_manager_connect_and_getters():
    """connect() and getters; get_vector(user_id) returns per-user long-term vector."""
    mock_short = MagicMock()
    mock_short.connect = AsyncMock()
    mock_task = MagicMock()
    mock_task.connect = AsyncMock()
    mock_summary = MagicMock()
    mock_summary.connect = AsyncMock()
    mock_vector = MagicMock()
    mock_user_data = MagicMock()
    mock_user_data.connect = AsyncMock()
    with patch("assistant.memory.manager.ShortTermMemory", return_value=mock_short):
        with patch("assistant.memory.manager.TaskMemory", return_value=mock_task):
            with patch("assistant.memory.manager.SummaryMemory", return_value=mock_summary):
                with patch("assistant.memory.manager.VectorMemory", return_value=mock_vector):
                    with patch(
                        "assistant.memory.manager.UserDataMemory", return_value=mock_user_data
                    ):
                        mgr = MemoryManager("redis://localhost:6379/0")
                        await mgr.connect()
                        mock_short.connect.assert_called_once()
                        mock_task.connect.assert_called_once()
                        mock_summary.connect.assert_called_once()
                        mock_user_data.connect.assert_called_once()
                        assert mgr.get_short_term() is mock_short
                        assert mgr.get_task_memory() is mock_task
                        assert mgr.get_summary() is mock_summary
                        assert mgr.get_vector("u1") is mock_vector
                        assert mgr.get_vector_short("u1") is mock_vector
                        assert mgr.get_user_data_memory() is mock_user_data


@pytest.mark.asyncio
async def test_memory_manager_clear_vector():
    """clear_vector(user_id, level) clears one user's vector level or all levels."""
    mgr = MemoryManager("redis://localhost:6379/0")
    mock_short = MagicMock()
    mock_medium = MagicMock()
    mock_long = MagicMock()
    mgr._vector_cache[("u1", VECTOR_LEVEL_SHORT)] = mock_short
    mgr._vector_cache[("u1", VECTOR_LEVEL_MEDIUM)] = mock_medium
    mgr._vector_cache[("u1", VECTOR_LEVEL_LONG)] = mock_long
    mgr.clear_vector(user_id="u1", level=VECTOR_LEVEL_SHORT)
    mock_short.clear.assert_called_once()
    mock_medium.clear.assert_not_called()
    mock_long.clear.assert_not_called()
    mgr.clear_vector(user_id="u1", level=None)
    assert mock_short.clear.call_count == 2
    assert mock_medium.clear.call_count == 1
    assert mock_long.clear.call_count == 1


@pytest.mark.asyncio
async def test_memory_manager_user_data():
    """get_user_data, set_user_data, clear_user_data delegate to UserDataMemory."""
    mgr = MemoryManager("redis://localhost:6379/0")
    mgr._user_data = MagicMock()
    mgr._user_data.get = AsyncMock(return_value={"name": "Alice"})
    mgr._user_data.set = AsyncMock()
    mgr._user_data.clear = AsyncMock()
    data = await mgr.get_user_data("u1")
    assert data == {"name": "Alice"}
    await mgr.set_user_data("u1", {"tz": "Europe/Moscow"})
    mgr._user_data.set.assert_called_once_with("u1", {"tz": "Europe/Moscow"})
    await mgr.clear_user_data("u1")
    mgr._user_data.clear.assert_called_once_with("u1")


@pytest.mark.asyncio
async def test_memory_manager_reset_memory_and_clear_short_term():
    """reset_memory(scope) and clear_short_term; vector clear is per user."""
    mgr = MemoryManager("redis://localhost:6379/0")
    mgr._short.clear = AsyncMock()
    mgr._summary.clear = AsyncMock()
    mgr._user_data.clear = AsyncMock()
    mock_short = MagicMock()
    mock_medium = MagicMock()
    mock_long = MagicMock()
    mgr._vector_cache[("u1", VECTOR_LEVEL_SHORT)] = mock_short
    mgr._vector_cache[("u1", VECTOR_LEVEL_MEDIUM)] = mock_medium
    mgr._vector_cache[("u1", VECTOR_LEVEL_LONG)] = mock_long
    await mgr.clear_short_term("u1", "s1")
    mgr._short.clear.assert_called_once_with("u1", "s1")
    await mgr.reset_memory("u1", scope="all")
    mock_short.clear.assert_called_once()
    mock_medium.clear.assert_called_once()
    mock_long.clear.assert_called_once()
    mgr._short.clear.assert_called_with("u1", "default")
    mgr._summary.clear.assert_called_once_with("u1", "default")
    mgr._user_data.clear.assert_called_once_with("u1")
