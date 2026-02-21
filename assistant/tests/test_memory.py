"""Tests for memory: short-term, task (requires Redis or mock)."""

import pytest

from assistant.memory.short_term import ShortTermMemory
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
