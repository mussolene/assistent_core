"""Tests for Event Bus (serialization, publish with optional Redis)."""

import pytest

from assistant.core.bus import _deserialize, _serialize
from assistant.core.events import IncomingMessage, OutgoingReply


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
    from assistant.core.bus import EventBus
    bus = EventBus("redis://localhost:6379/12")
    await bus.connect()
    await bus.publish_incoming(IncomingMessage(message_id="m1", user_id="u1", chat_id="c1", text="test"))
    await bus.disconnect()
