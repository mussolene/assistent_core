"""Tests for Event Bus (serialization, publish with optional Redis)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.core.bus import (
    CH_INCOMING,
    EventBus,
    _deserialize,
    _serialize,
)
from assistant.core.events import IncomingMessage, OutgoingReply, StreamToken


def test_serialize_deserialize_incoming():
    payload = IncomingMessage(message_id="1", user_id="2", chat_id="3", text="hi")
    raw = _serialize(payload)
    back = _deserialize(raw.encode("utf-8"), IncomingMessage)
    assert back.user_id == "2"
    assert back.text == "hi"


def test_serialize_deserialize_outgoing():
    payload = OutgoingReply(task_id="t1", chat_id="c1", text="ok", done=True)
    raw = _serialize(payload)
    back = _deserialize(raw.encode("utf-8"), OutgoingReply)
    assert back.task_id == "t1"
    assert back.done is True


@pytest.mark.asyncio
async def test_bus_connect_and_publish():
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/12")
        await r.ping()
        await r.close()
    except Exception:
        pytest.skip("Redis not available")
    bus = EventBus("redis://localhost:6379/12")
    await bus.connect()
    await bus.publish_incoming(
        IncomingMessage(message_id="m1", user_id="u1", chat_id="c1", text="test")
    )
    await bus.disconnect()


@pytest.mark.asyncio
async def test_bus_with_mock_redis():
    """EventBus connect, publish_*, subscribe_*, _ensure_connected with mocked redis."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_pubsub.listen = AsyncMock(return_value=iter([]))
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        bus = EventBus("redis://fake:6379/0")
        await bus.connect()
        assert bus._client is not None
        await bus.publish_incoming(
            IncomingMessage(message_id="m1", user_id="u1", chat_id="c1", text="hi")
        )
        from assistant.core.events import TaskCreated

        await bus.publish_task_created(
            TaskCreated(task_id="t1", user_id="u1", chat_id="c1", message_id="m1")
        )
        from assistant.core.events import AgentResult

        await bus.publish_agent_result(
            AgentResult(task_id="t1", agent_type="assistant", success=True, output_text="ok")
        )
        await bus.publish_outgoing(
            OutgoingReply(task_id="t1", chat_id="c1", message_id="m1", text="ok", done=True)
        )
        await bus.publish_stream_token(
            StreamToken(task_id="t1", chat_id="c1", message_id="m1", token="x", done=False)
        )
        mock_client.publish.assert_called()
        await bus.disconnect()
        mock_client.close.assert_called_once()


@pytest.mark.asyncio
async def test_bus_subscribe_and_run_listener_one_message():
    """run_listener processes one message and dispatches to handler."""
    received: list = []

    async def on_incoming(p: IncomingMessage) -> None:
        received.append(("incoming", p))
        bus.stop()

    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    payload_bytes = _serialize(
        IncomingMessage(message_id="m1", user_id="u1", chat_id="c1", text="hello")
    ).encode("utf-8")

    async def listen():
        yield {"type": "message", "channel": CH_INCOMING.encode("utf-8"), "data": payload_bytes}

    mock_pubsub.listen = listen
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        bus = EventBus("redis://fake:6379/0")
        bus.subscribe_incoming(on_incoming)
        await bus.connect()
        await bus.run_listener()
    assert len(received) == 1
    assert received[0][0] == "incoming"
    assert received[0][1].text == "hello"


@pytest.mark.asyncio
async def test_bus_ensure_connected_on_first_publish():
    """publish_* without connect() calls connect() via _ensure_connected."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        bus = EventBus("redis://fake:6379/0")
        assert bus._client is None
        await bus.publish_incoming(
            IncomingMessage(message_id="m1", user_id="u1", chat_id="c1", text="hi")
        )
        assert bus._client is not None
        mock_client.ping.assert_called_once()
        mock_client.publish.assert_called_once()
    await bus.disconnect()


@pytest.mark.asyncio
async def test_bus_run_listener_skips_non_message():
    """run_listener skips messages with type != 'message'."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    async def listen():
        yield {"type": "subscribe"}
        yield {"type": "message", "channel": b"other", "data": b"{}"}

    mock_pubsub.listen = listen
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        bus = EventBus("redis://fake:6379/0")
        received = []

        async def on_incoming(_):
            received.append(1)
            bus.stop()

        bus.subscribe_incoming(on_incoming)
        await bus.connect()
        await bus.run_listener()
    assert len(received) == 0


@pytest.mark.asyncio
async def test_bus_run_listener_skips_none_data():
    """run_listener skips when message data is None."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    async def listen():
        yield {"type": "message", "channel": CH_INCOMING.encode("utf-8"), "data": None}
        bus.stop()

    bus = None

    async def on_incoming(_):
        pass

    def get_bus():
        nonlocal bus
        bus = EventBus("redis://fake:6379/0")
        bus.subscribe_incoming(on_incoming)
        return bus

    mock_pubsub.listen = listen
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        bus = EventBus("redis://fake:6379/0")
        bus.subscribe_incoming(on_incoming)
        await bus.connect()
        await bus.run_listener()
    mock_pubsub.unsubscribe.assert_called_once()


@pytest.mark.asyncio
async def test_bus_run_listener_deserialize_error_logged():
    """run_listener logs warning and skips on deserialize error."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    payload_bad = b"not valid json"
    call_count = [0]

    async def listen():
        call_count[0] += 1
        yield {"type": "message", "channel": CH_INCOMING.encode("utf-8"), "data": payload_bad}
        bus.stop()

    bus = EventBus("redis://fake:6379/0")
    bus.subscribe_incoming(lambda _: None)
    mock_pubsub.listen = listen
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        await bus.connect()
        await bus.run_listener()
    assert call_count[0] >= 1


@pytest.mark.asyncio
async def test_bus_run_listener_handler_exception_logged():
    """run_listener logs exception when handler raises."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.close = AsyncMock()
    mock_pubsub = MagicMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    payload_bytes = _serialize(
        IncomingMessage(message_id="m1", user_id="u1", chat_id="c1", text="x")
    ).encode("utf-8")

    async def listen():
        yield {"type": "message", "channel": CH_INCOMING.encode("utf-8"), "data": payload_bytes}
        bus.stop()

    bus = EventBus("redis://fake:6379/0")

    async def failing_handler(_):
        raise ValueError("handler failed")

    bus.subscribe_incoming(failing_handler)
    mock_pubsub.listen = listen
    mock_client.pubsub = MagicMock(return_value=mock_pubsub)

    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        await bus.connect()
        await bus.run_listener()
    mock_pubsub.unsubscribe.assert_called_once()


@pytest.mark.asyncio
async def test_bus_disconnect_without_pubsub():
    """disconnect() works when _pubsub is None (only client)."""
    mock_client = MagicMock()
    mock_client.ping = AsyncMock()
    mock_client.close = AsyncMock()
    with patch("assistant.core.bus.aioredis") as m:
        m.from_url = MagicMock(return_value=mock_client)
        bus = EventBus("redis://fake:6379/0")
        await bus.connect()
        assert bus._pubsub is None
        await bus.disconnect()
        assert bus._client is None
        mock_client.close.assert_called_once()
