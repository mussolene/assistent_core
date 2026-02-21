"""Tests for agents (Assistant, Tool) with mocks."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.agents.assistant import AssistantAgent
from assistant.agents.base import TaskContext
from assistant.agents.tool_agent import ToolAgent
from assistant.skills.registry import SkillRegistry
from assistant.skills.runner import SandboxRunner


def _ctx(**kwargs):
    return TaskContext(
        task_id=kwargs.get("task_id", "t1"),
        user_id=kwargs.get("user_id", "u1"),
        chat_id=kwargs.get("chat_id", "c1"),
        channel=kwargs.get("channel", "telegram"),
        message_id=kwargs.get("message_id", "m1"),
        text=kwargs.get("text", "hello"),
        reasoning_requested=kwargs.get("reasoning_requested", False),
        state=kwargs.get("state", "assistant"),
        iteration=kwargs.get("iteration", 0),
        tool_results=kwargs.get("tool_results", []),
        metadata=kwargs.get("metadata", {}),
    )


@pytest.mark.asyncio
async def test_assistant_agent_returns_text():
    model = MagicMock()
    model.generate = AsyncMock(return_value="Hi there!")
    memory = MagicMock()
    memory.get_context_for_user = AsyncMock(return_value=[])
    memory.append_message = AsyncMock()
    agent = AssistantAgent(model, memory)
    result = await agent.handle(_ctx())
    assert result.success is True
    assert "Hi there" in result.output_text


@pytest.mark.asyncio
async def test_tool_agent_runs_skill():
    reg = SkillRegistry()
    runner = SandboxRunner()
    memory = MagicMock()
    memory.append_tool_result = AsyncMock()
    import tempfile

    from assistant.skills.filesystem import FilesystemSkill
    with tempfile.TemporaryDirectory() as d:
        reg.register(FilesystemSkill(workspace_dir=d))
        agent = ToolAgent(reg, runner, memory)
        ctx = _ctx(metadata={"pending_tool_calls": [{"name": "filesystem", "params": {"action": "list", "path": "."}}]})
        result = await agent.handle(ctx)
    assert result.success is True
    assert result.next_agent == "assistant"
    assert result.metadata and "tool_results" in result.metadata


@pytest.mark.asyncio
async def test_tool_agent_unknown_skill():
    reg = SkillRegistry()
    runner = SandboxRunner()
    memory = MagicMock()
    memory.append_tool_result = AsyncMock()
    agent = ToolAgent(reg, runner, memory)
    ctx = _ctx(metadata={"pending_tool_calls": [{"name": "nonexistent", "params": {}}]})
    result = await agent.handle(ctx)
    assert result.success is True
    assert result.metadata
    assert any(r.get("ok") is False for r in result.metadata.get("tool_results", []))
