"""Tests for orchestrator state and task manager (with mocks)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.agents.base import AgentResult, TaskContext
from assistant.core.agent_registry import AgentRegistry
from assistant.core.events import IncomingMessage, StreamToken
from assistant.core.orchestrator import Orchestrator
from assistant.core.task_manager import TaskManager


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
    result = await reg.handle("assistant", ctx)
    assert result.success is True
    assert result.output_text == "ok"


# --- Orchestrator stream_callback and StreamToken ---


def _make_orchestrator_with_mock_bus():
    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    bus = MagicMock()
    bus.publish_stream_token = AsyncMock()
    orch = Orchestrator(config=config, bus=bus)
    return orch, bus


def _make_incoming_payload(chat_id: str = "chat_1", message_id: str = "msg_1"):
    return IncomingMessage(
        message_id=message_id,
        user_id="user_1",
        chat_id=chat_id,
        text="hello",
    )


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_stream_callback_set_when_assistant_and_stream():
    """When state is assistant and stream True, stream_callback is set and publishes StreamToken."""
    orch, bus = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload(chat_id="c1")
    task_data = {
        "state": "assistant",
        "stream": True,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
        "tool_results": [{"tool": "test", "result": "ok"}],
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    stream_cb = ctx.metadata.get("stream_callback")
    assert stream_cb is not None

    await stream_cb("hi", False)
    bus.publish_stream_token.assert_called_once()
    call_arg = bus.publish_stream_token.call_args[0][0]
    assert isinstance(call_arg, StreamToken)
    assert call_arg.task_id == "tid_1"
    assert call_arg.chat_id == "c1"
    assert call_arg.token == "hi"
    assert call_arg.done is False

    bus.publish_stream_token.reset_mock()
    await stream_cb("", True)
    bus.publish_stream_token.assert_called_once()
    call_arg = bus.publish_stream_token.call_args[0][0]
    assert call_arg.token == ""
    assert call_arg.done is True


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_stream_callback_none_when_not_assistant():
    """When state is not assistant, stream_callback is None."""
    orch, _ = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload()
    task_data = {
        "state": "tool",
        "stream": True,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    assert ctx.metadata.get("stream_callback") is None


@pytest.mark.asyncio
async def test_orchestrator_task_to_context_stream_callback_none_when_stream_disabled():
    """When stream is False, stream_callback is None."""
    orch, _ = _make_orchestrator_with_mock_bus()
    payload = _make_incoming_payload()
    task_data = {
        "state": "assistant",
        "stream": False,
        "chat_id": "c1",
        "task_id": "tid_1",
        "user_id": "u1",
        "message_id": "m1",
    }
    ctx = orch._task_to_context("tid_1", task_data, payload)
    assert ctx.metadata.get("stream_callback") is None


@pytest.mark.asyncio
async def test_orchestrator_start_stop():
    """start() connects bus and tasks, stop() disconnects."""
    config = MagicMock()
    config.redis.url = "redis://localhost:6379/0"
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False
    bus = MagicMock()
    bus.connect = AsyncMock()
    bus.disconnect = AsyncMock()
    bus.stop = MagicMock()
    bus.subscribe_incoming = MagicMock()
    with patch("assistant.core.orchestrator.TaskManager") as TM:
        tasks = MagicMock()
        tasks.connect = AsyncMock()
        TM.return_value = tasks
        orch = Orchestrator(config=config, bus=bus)
        await orch.start()
        bus.connect.assert_called_once()
        tasks.connect.assert_called_once()
        bus.subscribe_incoming.assert_called_once()
        assert orch._running is True
        await orch.stop()
        assert orch._running is False
        bus.stop.assert_called_once()
        bus.disconnect.assert_called_once()
