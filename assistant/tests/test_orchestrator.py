"""Tests for orchestrator state and task manager (with mocks)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.core.task_manager import TaskManager
from assistant.core.agent_registry import AgentRegistry
from assistant.agents.base import TaskContext, AgentResult


@pytest.mark.asyncio
async def test_task_manager_requires_redis():
    tm = TaskManager("redis://localhost:6379/14")
    try:
        await tm.connect()
    except Exception:
        pytest.skip("Redis not available")
    task_id = await tm.create(
        user_id="u1",
        chat_id="c1",
        text="hello",
    )
    assert task_id
    task = await tm.get(task_id)
    assert task["user_id"] == "u1"
    assert task["state"] == "received"
    await tm.update(task_id, state="assistant")
    task2 = await tm.get(task_id)
    assert task2["state"] == "assistant"


@pytest.mark.asyncio
async def test_agent_registry_unknown():
    reg = AgentRegistry()
    ctx = TaskContext(
        task_id="t1",
        user_id="u1",
        chat_id="c1",
        channel="telegram",
        message_id="m1",
        text="hi",
        reasoning_requested=False,
        state="assistant",
        iteration=0,
        tool_results=[],
        metadata={},
    )
    result = await reg.handle("unknown_agent", ctx)
    assert result.success is False
    assert "unknown" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_agent_registry_mock():
    reg = AgentRegistry()
    async def mock_handle(ctx):
        return AgentResult(success=True, output_text="ok")
    mock = MagicMock()
    mock.handle = AsyncMock(side_effect=mock_handle)
    reg.register("assistant", mock)
    ctx = TaskContext(
        task_id="t1", user_id="u1", chat_id="c1", channel="telegram",
        message_id="m1", text="hi", reasoning_requested=False,
        state="assistant", iteration=0, tool_results=[], metadata={},
    )
    result = await reg.handle("assistant", ctx)
    assert result.success is True
    assert result.output_text == "ok"
