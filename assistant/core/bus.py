"""Event Bus: Redis pub/sub for events. Publish and subscribe with typed payloads."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable, Awaitable

import redis.asyncio as aioredis
from pydantic import BaseModel

from assistant.core.events import (
    AgentResult,
    IncomingMessage,
    OutgoingReply,
    StreamToken,
    TaskCreated,
)

logger = logging.getLogger(__name__)

# Channel names
CH_INCOMING = "assistant:incoming_message"
CH_TASK_CREATED = "assistant:task_created"
CH_AGENT_RESULT = "assistant:agent_result"
CH_OUTGOING = "assistant:outgoing_reply"
CH_STREAM = "assistant:stream_token"


def _serialize(payload: BaseModel) -> str:
    return payload.model_dump_json()


def _deserialize(raw: bytes, model: type[BaseModel]) -> BaseModel:
    return model.model_validate_json(raw.decode("utf-8"))


class EventBus:
    """Redis-backed event bus. Publish events and subscribe with async handlers."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._handlers: dict[str, list[Callable[..., Awaitable[None]]]] = {}
        self._running = False

    async def connect(self) -> None:
        if self._client is None:
            self._client = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=False,
            )
            await self._client.ping()
        logger.info("EventBus connected to Redis")

    async def disconnect(self) -> None:
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None
        if self._client:
            await self._client.close()
            self._client = None
        self._running = False

    async def publish_incoming(self, payload: IncomingMessage) -> None:
        await self._ensure_connected()
        await self._client.publish(CH_INCOMING, _serialize(payload))
        logger.debug("published incoming_message", extra={"message_id": payload.message_id})

    async def publish_task_created(self, payload: TaskCreated) -> None:
        await self._ensure_connected()
        await self._client.publish(CH_TASK_CREATED, _serialize(payload))
        logger.debug("published task_created", extra={"task_id": payload.task_id})

    async def publish_agent_result(self, payload: AgentResult) -> None:
        await self._ensure_connected()
        await self._client.publish(CH_AGENT_RESULT, _serialize(payload))
        logger.debug("published agent_result", extra={"task_id": payload.task_id})

    async def publish_outgoing(self, payload: OutgoingReply) -> None:
        await self._ensure_connected()
        await self._client.publish(CH_OUTGOING, _serialize(payload))
        logger.debug("published outgoing_reply", extra={"task_id": payload.task_id})

    async def publish_stream_token(self, payload: StreamToken) -> None:
        await self._ensure_connected()
        await self._client.publish(CH_STREAM, _serialize(payload))

    async def _ensure_connected(self) -> None:
        if self._client is None:
            await self.connect()

    def subscribe_incoming(self, handler: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        self._handlers.setdefault(CH_INCOMING, []).append(handler)

    def subscribe_task_created(self, handler: Callable[[TaskCreated], Awaitable[None]]) -> None:
        self._handlers.setdefault(CH_TASK_CREATED, []).append(handler)

    def subscribe_agent_result(self, handler: Callable[[AgentResult], Awaitable[None]]) -> None:
        self._handlers.setdefault(CH_AGENT_RESULT, []).append(handler)

    def subscribe_outgoing(self, handler: Callable[[OutgoingReply], Awaitable[None]]) -> None:
        self._handlers.setdefault(CH_OUTGOING, []).append(handler)

    def subscribe_stream(self, handler: Callable[[StreamToken], Awaitable[None]]) -> None:
        self._handlers.setdefault(CH_STREAM, []).append(handler)

    _channel_models = {
        CH_INCOMING: IncomingMessage,
        CH_TASK_CREATED: TaskCreated,
        CH_AGENT_RESULT: AgentResult,
        CH_OUTGOING: OutgoingReply,
        CH_STREAM: StreamToken,
    }

    async def run_listener(self) -> None:
        """Run the pub/sub listener and dispatch to handlers. Blocks until stop."""
        await self._ensure_connected()
        self._pubsub = self._client.pubsub()
        channels = list(self._channel_models.keys())
        await self._pubsub.subscribe(*channels)
        self._running = True
        logger.info("EventBus listener started", extra={"channels": channels})
        try:
            async for message in self._pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue
                ch = message["channel"]
                if isinstance(ch, bytes):
                    ch = ch.decode("utf-8")
                data = message.get("data")
                if data is None:
                    continue
                model_cls = self._channel_models.get(ch)
                if not model_cls or not data:
                    continue
                try:
                    payload = _deserialize(data, model_cls)
                except Exception as e:
                    logger.warning("failed to deserialize event", extra={"channel": ch, "error": str(e)})
                    continue
                for handler in self._handlers.get(ch, []):
                    try:
                        await handler(payload)
                    except Exception as e:
                        logger.exception("handler failed for %s: %s", ch, e)
        finally:
            await self._pubsub.unsubscribe()
            self._running = False

    def stop(self) -> None:
        self._running = False
