"""Tests for memory: short-term, task, summary, manager (Redis or mocks)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.memory.manager import MemoryManager
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
    mgr._short.get_messages = AsyncMock(return_value=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    mgr._summary = MagicMock()
    mgr._summary.get_summary = AsyncMock(return_value="Old summary.")
    mgr._vector = MagicMock()
    mgr._vector._get_model = MagicMock(return_value=None)
    mgr._task = MagicMock()
    mgr._task.get_tool_results = AsyncMock(return_value=[])
    ctx = await mgr.get_context_for_user("u1", "task1", include_vector=True)
    assert any("Old summary" in str(m.get("content", "")) for m in ctx)
    assert any(m.get("content") == "hi" for m in ctx)
