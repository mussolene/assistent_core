"""Integration test: incoming -> orchestrator -> assistant -> stream/outgoing (mocked bus and model)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.agents.assistant import AssistantAgent
from assistant.core.events import IncomingMessage, OutgoingReply, StreamToken
from assistant.core.orchestrator import Orchestrator
from assistant.core.task_manager import TaskManager


@pytest.mark.asyncio
async def test_incoming_to_stream_and_outgoing_mocked():
    """Orchestrator _process_task with mock bus and task storage; assistant streams tokens and final reply."""
    stream_tokens: list[StreamToken] = []
    outgoing_replies: list[OutgoingReply] = []

    mock_bus = MagicMock()
    mock_bus.publish_stream_token = AsyncMock(side_effect=lambda p: stream_tokens.append(p))
    mock_bus.publish_outgoing = AsyncMock(side_effect=lambda p: outgoing_replies.append(p))

    config = MagicMock()
    config.orchestrator.max_iterations = 5
    config.orchestrator.autonomous_mode = False

    stored: dict = {}

    async def mock_set(key: str, value: str, ex: int | None = None) -> None:
        stored[key] = value

    async def mock_get(key: str) -> str | None:
        return stored.get(key)

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=mock_set)
    mock_redis.get = AsyncMock(side_effect=mock_get)

    with patch("assistant.core.task_manager.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_redis)
        tm = TaskManager("redis://localhost:6379/0")
        await tm.connect()

    mock_gateway = MagicMock()
    async def mock_generate(prompt, *, stream=False, **kw):
        if stream:
            async def gen():
                yield "Hello"
                yield " world"
            return gen()
        return "Hello world"

    mock_gateway.generate = AsyncMock(side_effect=mock_generate)
    mock_memory = MagicMock()
    mock_memory.get_context_for_user = AsyncMock(return_value=[])
    mock_memory.append_message = AsyncMock()
    assistant = AssistantAgent(model_gateway=mock_gateway, memory=mock_memory)
    orch = Orchestrator(config=config, bus=mock_bus)
    orch._tasks = tm
    orch._agents.register("assistant", assistant)
    orch._agents.register("tool", MagicMock())

    payload = IncomingMessage(
        message_id="m1",
        user_id="u1",
        chat_id="c1",
        text="Hi",
    )
    task_id = await orch._tasks.create(
        user_id=payload.user_id,
        chat_id=payload.chat_id,
        channel=payload.channel.value,
        message_id=payload.message_id,
        text=payload.text,
        reasoning_requested=False,
        stream=True,
    )
    await orch._process_task(task_id, payload)

    assert len(outgoing_replies) >= 1
    last = outgoing_replies[-1]
    assert last.done is True
    assert "Hello" in last.text or "world" in last.text
    assert any(st.token for st in stream_tokens) or last.text
