"""Orchestrator: deterministic state machine. Subscribes to Event Bus, dispatches to agents."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from assistant.core.bus import EventBus, CH_INCOMING, CH_OUTGOING
from assistant.core.events import IncomingMessage, OutgoingReply
from assistant.core.task_manager import TaskManager
from assistant.core.agent_registry import AgentRegistry
from assistant.agents.base import TaskContext

if TYPE_CHECKING:
    from assistant.config.loader import Config

logger = logging.getLogger(__name__)


class Orchestrator:
    """State-driven orchestrator. No LLM in lifecycle decisions."""

    def __init__(self, config: "Config", bus: EventBus) -> None:
        self._config = config
        self._bus = bus
        self._tasks = TaskManager(config.redis.url)
        self._agents = AgentRegistry()
        self._running = False

    async def start(self) -> None:
        await self._bus.connect()
        await self._tasks.connect()
        self._bus.subscribe_incoming(self._on_incoming)
        self._running = True
        logger.info("Orchestrator started")

    async def stop(self) -> None:
        self._running = False
        self._bus.stop()
        await self._bus.disconnect()

    async def run_forever(self) -> None:
        """Run the event bus listener (blocks)."""
        await self._bus.run_listener()

    async def _on_incoming(self, payload: IncomingMessage) -> None:
        task_id = await self._tasks.create(
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            channel=payload.channel.value,
            message_id=payload.message_id,
            text=payload.text,
            reasoning_requested=payload.reasoning_requested,
        )
        asyncio.create_task(self._process_task(task_id, payload))

    async def _process_task(self, task_id: str, payload: IncomingMessage) -> None:
        max_iterations = self._config.orchestrator.max_iterations
        autonomous = self._config.orchestrator.autonomous_mode
        if not autonomous:
            max_iterations = min(max_iterations, 1)
        state = "assistant"
        iteration = 0
        last_output = ""
        while iteration < max_iterations:
            task_data = await self._tasks.get(task_id)
            if not task_data:
                break
            context = self._task_to_context(task_id, task_data, payload)
            result = await self._agents.handle(state, context)
            if not result.success:
                await self._bus.publish_outgoing(
                    OutgoingReply(
                        task_id=task_id,
                        chat_id=payload.chat_id,
                        message_id=payload.message_id,
                        text=result.error or "Error",
                        done=True,
                    )
                )
                break
            last_output = result.output_text
            if result.next_agent:
                state = result.next_agent
                iteration += 1
                if state == "tool" and result.tool_calls:
                    await self._tasks.update(
                        task_id,
                        state="tool",
                        pending_tool_calls=result.tool_calls,
                        iteration=iteration,
                    )
                    continue
                if state == "assistant":
                    tool_results = (result.metadata or {}).get("tool_results", [])
                    await self._tasks.update(
                        task_id,
                        state="assistant",
                        tool_results=tool_results,
                        pending_tool_calls=[],
                        iteration=iteration,
                    )
                    continue
            else:
                await self._bus.publish_outgoing(
                    OutgoingReply(
                        task_id=task_id,
                        chat_id=payload.chat_id,
                        message_id=payload.message_id,
                        text=last_output,
                        done=True,
                    )
                )
                break
        if iteration >= max_iterations and last_output:
            await self._bus.publish_outgoing(
                OutgoingReply(
                    task_id=task_id,
                    chat_id=payload.chat_id,
                    message_id=payload.message_id,
                    text=last_output,
                    done=True,
                )
            )

    def _task_to_context(
        self,
        task_id: str,
        task_data: dict,
        payload: IncomingMessage,
    ) -> TaskContext:
        return TaskContext(
            task_id=task_id,
            user_id=task_data.get("user_id", payload.user_id),
            chat_id=task_data.get("chat_id", payload.chat_id),
            channel=task_data.get("channel", "telegram"),
            message_id=task_data.get("message_id", payload.message_id),
            text=task_data.get("text", payload.text),
            reasoning_requested=task_data.get("reasoning_requested", False),
            state=task_data.get("state", "assistant"),
            iteration=task_data.get("iteration", 0),
            tool_results=task_data.get("tool_results", []),
            metadata={
                "pending_tool_calls": task_data.get("pending_tool_calls", []),
                "stream": task_data.get("stream", False),
            },
        )

    def set_agent_registry(self, registry: AgentRegistry) -> None:
        self._agents = registry
