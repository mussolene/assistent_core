"""Orchestrator: deterministic state machine. Subscribes to Event Bus, dispatches to agents."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from assistant.agents.base import TaskContext
from assistant.core.agent_registry import AgentRegistry
from assistant.core.bus import EventBus
from assistant.core.events import IncomingMessage, OutgoingReply, StreamToken
from assistant.core.task_manager import TaskManager

if TYPE_CHECKING:
    from assistant.config.loader import Config

logger = logging.getLogger(__name__)


class Orchestrator:
    """State-driven orchestrator. No LLM in lifecycle decisions."""

    def __init__(
        self,
        config: "Config",
        bus: EventBus,
        memory: Any = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._memory = memory
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
            stream=True,
        )
        asyncio.create_task(self._process_task(task_id, payload))

    async def _process_task(self, task_id: str, payload: IncomingMessage) -> None:
        # Вложения: извлечь текст, положить в вектор, сохранить ссылки в Redis (файлы не храним)
        if payload.attachments and self._memory:
            try:
                from assistant.dashboard.config_store import get_config_from_redis_sync
                from assistant.core.file_indexing import index_telegram_attachments

                redis_cfg = get_config_from_redis_sync(self._config.redis.url)
                bot_token = (redis_cfg.get("TELEGRAM_BOT_TOKEN") or "").strip()
                if bot_token:
                    ref_ids = await index_telegram_attachments(
                        self._config.redis.url,
                        self._memory,
                        payload.user_id,
                        payload.chat_id,
                        payload.attachments,
                        bot_token,
                    )
                    if ref_ids:
                        if not payload.text.strip():
                            payload.text = (
                                "[Индексированы файлы в память. Можешь спросить по содержимому или попросить «отправь файл …».]"
                            )
                        await self._tasks.update(task_id, text=payload.text)
            except Exception as e:
                logger.exception("File indexing: %s", e)

        max_iterations = self._config.orchestrator.max_iterations
        autonomous = self._config.orchestrator.autonomous_mode
        if not autonomous:
            # Больше шагов без автономного режима: считаем только переходы assistant→tool
            max_iterations = max(4, min(max_iterations, 8))
        state = "assistant"
        iteration = 0  # увеличивается только при переходе assistant → tool
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
                        channel=payload.channel,
                    )
                )
                break
            if result.tool_calls:
                last_output = ""
            else:
                last_output = result.output_text
            if result.next_agent:
                next_state = result.next_agent
                # Считаем только переход assistant → tool, чтобы не сжигать лимит на возврат из tool
                if state == "assistant" and next_state == "tool":
                    iteration += 1
                state = next_state
                if state == "tool" and result.tool_calls:
                    await self._tasks.update(
                        task_id,
                        state="tool",
                        pending_tool_calls=result.tool_calls,
                        iteration=iteration,
                    )
                    continue
                if state == "assistant":  # tool → assistant: iteration не увеличиваем
                    tool_results = (result.metadata or {}).get("tool_results", [])
                    for tr in tool_results:
                        if isinstance(tr, dict) and tr.get("user_reply"):
                            await self._bus.publish_outgoing(
                                OutgoingReply(
                                    task_id=task_id,
                                    chat_id=payload.chat_id,
                                    message_id=payload.message_id,
                                    text=tr.get("user_reply", ""),
                                    done=True,
                                    channel=payload.channel,
                                )
                            )
                            return
                        if (
                            isinstance(tr, dict)
                            and tr.get("formatted")
                            and tr.get("inline_keyboard")
                        ):
                            await self._bus.publish_outgoing(
                                OutgoingReply(
                                    task_id=task_id,
                                    chat_id=payload.chat_id,
                                    message_id=payload.message_id,
                                    text=tr.get("formatted", ""),
                                    done=True,
                                    channel=payload.channel,
                                    reply_markup={"inline_keyboard": tr["inline_keyboard"]},
                                )
                            )
                            return
                    await self._tasks.update(
                        task_id,
                        state="assistant",
                        tool_results=tool_results,
                        pending_tool_calls=[],
                        iteration=iteration,
                    )
                    continue
            else:
                task_data = await self._tasks.get(task_id)
                send_doc = self._get_send_document_from_tool_results(task_data)
                send_checklist = self._get_send_checklist_from_tool_results(task_data)
                await self._bus.publish_outgoing(
                    OutgoingReply(
                        task_id=task_id,
                        chat_id=payload.chat_id,
                        message_id=payload.message_id,
                        text=last_output,
                        done=True,
                        channel=payload.channel,
                        send_document=send_doc,
                        send_checklist=send_checklist,
                    )
                )
                break
        if iteration >= max_iterations:
            text_to_send = last_output.strip() if last_output else (
                "Превышено число шагов. Ответьте коротко или упростите запрос (например: «напомни про задачу X через 5 минут»)."
            )
            if text_to_send:
                task_data = await self._tasks.get(task_id)
                send_doc = self._get_send_document_from_tool_results(task_data)
                send_checklist = self._get_send_checklist_from_tool_results(task_data)
                await self._bus.publish_outgoing(
                    OutgoingReply(
                        task_id=task_id,
                        chat_id=payload.chat_id,
                        message_id=payload.message_id,
                        text=text_to_send,
                        done=True,
                        channel=payload.channel,
                        send_document=send_doc,
                        send_checklist=send_checklist,
                    )
                )

    def _task_to_context(
        self,
        task_id: str,
        task_data: dict,
        payload: IncomingMessage,
    ) -> TaskContext:
        state = task_data.get("state", "assistant")
        stream = task_data.get("stream", True)
        stream_callback = None
        # Стримим только ответы после выполнения инструментов (чтобы не слать пользователю сырой JSON tool_calls)
        has_tool_results = bool(task_data.get("tool_results"))
        if state == "assistant" and stream and has_tool_results:
            chat_id = task_data.get("chat_id", payload.chat_id)
            ch = payload.channel

            async def _stream_cb(tok: str, done: bool = False) -> None:
                await self._bus.publish_stream_token(
                    StreamToken(task_id=task_id, chat_id=chat_id, token=tok, done=done, channel=ch)
                )

            stream_callback = _stream_cb
        metadata = {
            "pending_tool_calls": task_data.get("pending_tool_calls", []),
            "stream": stream,
            "stream_callback": stream_callback,
        }
        return TaskContext(
            task_id=task_id,
            user_id=task_data.get("user_id", payload.user_id),
            chat_id=task_data.get("chat_id", payload.chat_id),
            channel=task_data.get("channel", "telegram"),
            message_id=task_data.get("message_id", payload.message_id),
            text=task_data.get("text", payload.text),
            reasoning_requested=task_data.get("reasoning_requested", False),
            state=state,
            iteration=task_data.get("iteration", 0),
            tool_results=task_data.get("tool_results", []),
            metadata=metadata,
        )

    @staticmethod
    def _get_send_document_from_tool_results(task_data: dict | None) -> dict | None:
        """Взять send_document из последнего tool_result (для отправки файла в чат)."""
        if not task_data:
            return None
        for tr in reversed(task_data.get("tool_results") or []):
            if isinstance(tr, dict) and tr.get("send_document"):
                return tr["send_document"]
        return None

    @staticmethod
    def _get_send_checklist_from_tool_results(task_data: dict | None) -> dict | None:
        """Взять send_checklist из последнего tool_result (чеклист в чат)."""
        if not task_data:
            return None
        for tr in reversed(task_data.get("tool_results") or []):
            if isinstance(tr, dict) and tr.get("send_checklist"):
                return tr["send_checklist"]
        return None

    def set_agent_registry(self, registry: AgentRegistry) -> None:
        self._agents = registry
