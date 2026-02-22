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


def _format_attachment_paths_for_context(attachments: list[dict], user_id: str) -> str:
    """Формирует подсказку для ассистента: пути вложений для вызова index_document при необходимости."""
    paths = [(a.get("path"), a.get("filename") or "файл") for a in attachments if a.get("path")]
    if not paths:
        return ""
    parts = [f"{p} ({name})" for p, name in paths]
    return f"Вложения с путями: {', '.join(parts)}. Для индексации в Qdrant: index_document(path=<путь>, user_id={user_id})."


class Orchestrator:
    """State-driven orchestrator. No LLM in lifecycle decisions."""

    def __init__(
        self,
        config: "Config",
        bus: EventBus,
        memory: Any = None,
        gateway_factory: Any = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._memory = memory
        self._gateway_factory = gateway_factory
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

    def _schedule_conversation_index(self, user_id: str, chat_id: str) -> None:
        """Итерация 8.1: фоново индексировать последние сообщения в Qdrant (conversation memory)."""
        asyncio.create_task(self._index_conversation_memory_background(user_id, chat_id))

    async def _index_conversation_memory_background(self, user_id: str, chat_id: str) -> None:
        if not self._memory:
            return
        try:
            short = self._memory.get_short_term()
            messages = await short.get_messages(user_id, "default")
            if not messages:
                return
            from assistant.core.qdrant_docs import (
                get_qdrant_url,
                index_conversation_to_qdrant,
            )

            qdrant_url = get_qdrant_url(self._config.redis.url)
            if not qdrant_url:
                return
            loop = asyncio.get_event_loop()
            cnt, err = await loop.run_in_executor(
                None,
                lambda: index_conversation_to_qdrant(
                    messages,
                    user_id,
                    chat_id,
                    qdrant_url,
                    redis_url=self._config.redis.url,
                ),
            )
            if err:
                logger.debug("Conversation memory index: %s", err)
        except Exception as e:
            logger.debug("Conversation memory index background: %s", e)

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
        # Вложения: сразу ответ «Пошёл читать файл», затем индексация и ответ о содержании
        original_text = (payload.text or "").strip()
        if payload.attachments and self._memory:
            try:
                from assistant.core.file_indexing import index_telegram_attachments
                from assistant.dashboard.config_store import get_config_from_redis_sync

                # 1. Сразу сообщить пользователю
                await self._bus.publish_outgoing(
                    OutgoingReply(
                        task_id=task_id,
                        chat_id=payload.chat_id,
                        message_id=payload.message_id,
                        text="Пошёл читать файл…",
                        done=True,
                        channel=payload.channel,
                    )
                )
                redis_cfg = get_config_from_redis_sync(self._config.redis.url)
                bot_token = (redis_cfg.get("TELEGRAM_BOT_TOKEN") or "").strip()
                if not bot_token:
                    await self._bus.publish_outgoing(
                        OutgoingReply(
                            task_id=task_id,
                            chat_id=payload.chat_id,
                            message_id=payload.message_id,
                            text="Не удалось прочитать файл: бот не настроен.",
                            done=True,
                            channel=payload.channel,
                        )
                    )
                    self._schedule_conversation_index(payload.user_id, payload.chat_id)
                    return
                ref_ids, extracted_text = await index_telegram_attachments(
                    self._config.redis.url,
                    self._memory,
                    payload.user_id,
                    payload.chat_id,
                    payload.attachments,
                    bot_token,
                )
                # Итерация 3.3: вложения с path — индексируем также в Qdrant (если настроен)
                qdrant_indexed = 0
                try:
                    from assistant.core.qdrant_docs import get_qdrant_url, index_document_to_qdrant

                    qdrant_url = get_qdrant_url(self._config.redis.url)
                    if qdrant_url:
                        for att in payload.attachments:
                            path = att.get("path")
                            if path and isinstance(path, str):
                                cnt, err = index_document_to_qdrant(
                                    path,
                                    payload.user_id,
                                    qdrant_url,
                                    redis_url=self._config.redis.url,
                                    mime_type=att.get("mime_type") or "",
                                    filename=att.get("filename"),
                                )
                                if cnt > 0:
                                    qdrant_indexed += cnt
                                if err:
                                    logger.debug("Qdrant index %s: %s", path, err)
                except Exception as e:
                    logger.debug("Qdrant indexing for attachments: %s", e)
                paths_note = _format_attachment_paths_for_context(
                    payload.attachments, payload.user_id
                )
                if ref_ids:
                    if not original_text:
                        payload.text = "[Индексированы файлы в память. Можешь спросить по содержимому или попросить «отправь файл …».]"
                    else:
                        payload.text = original_text
                    if qdrant_indexed > 0:
                        payload.text += " [Документ также проиндексирован в Qdrant для поиска.]"
                    if paths_note:
                        payload.text += " " + paths_note
                    await self._tasks.update(task_id, text=payload.text)
                    # 2. Ответ о содержании: summary от модели или fallback
                    summary_text = await self._file_summary_for_user(extracted_text, ref_ids)
                    await self._bus.publish_outgoing(
                        OutgoingReply(
                            task_id=task_id,
                            chat_id=payload.chat_id,
                            message_id=payload.message_id,
                            text=summary_text,
                            done=True,
                            channel=payload.channel,
                        )
                    )
                    # Вопрос только про содержимое файла уже закрыт summary — не вызывать агента
                    if not original_text or self._is_only_file_content_question(original_text):
                        self._schedule_conversation_index(payload.user_id, payload.chat_id)
                        return
                elif paths_note:
                    # Вложения с path без индексации в локальную память — передаём пути ассистенту для index_document
                    payload.text = (
                        (payload.text or original_text or "[Вложение.]").strip() + " " + paths_note
                    )
                    await self._tasks.update(task_id, text=payload.text)
            except Exception as e:
                logger.exception("File indexing: %s", e)
                await self._bus.publish_outgoing(
                    OutgoingReply(
                        task_id=task_id,
                        chat_id=payload.chat_id,
                        message_id=payload.message_id,
                        text="Не удалось прочитать файл. Попробуйте позже или задайте вопрос текстом.",
                        done=True,
                        channel=payload.channel,
                    )
                )
                self._schedule_conversation_index(payload.user_id, payload.chat_id)
                if not original_text:
                    return

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
                            self._schedule_conversation_index(payload.user_id, payload.chat_id)
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
                            self._schedule_conversation_index(payload.user_id, payload.chat_id)
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
        self._schedule_conversation_index(payload.user_id, payload.chat_id)
        if iteration >= max_iterations:
            text_to_send = (
                last_output.strip()
                if last_output
                else (
                    "Превышено число шагов. Ответьте коротко или упростите запрос (например: «напомни про задачу X через 5 минут»)."
                )
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

    @staticmethod
    def _is_only_file_content_question(text: str) -> bool:
        """Вопрос только про содержимое файла/документа — ответ уже дали в summary, агента не вызываем."""
        t = (text or "").strip().lower()
        if len(t) > 120:
            return False
        file_phrases = (
            "что написано",
            "что тут написано",
            "что здесь написано",
            "что в файле",
            "что в документе",
            "о чём файл",
            "о чём документ",
            "опиши файл",
            "опиши документ",
            "что там написано",
            "что в этом файле",
            "что в этом документе",
            "что написано здесь",
            "что написано тут",
            "содержимое файла",
            "содержимое документа",
            "что на картинке",
            "что на изображении",
        )
        return any(p in t for p in file_phrases)

    async def _file_summary_for_user(self, extracted_text: str, ref_ids: list[str]) -> str:
        """Сгенерировать краткий ответ пользователю о содержании файла (модель или fallback)."""
        if not self._gateway_factory:
            return "Файл проиндексирован. Можешь спросить что в нём."
        # Только плейсхолдеры изображений или пусто — без извлечённого текста
        no_readable = not extracted_text.strip() or (
            "изображение" in extracted_text and len(extracted_text.strip()) < 120
        )
        if no_readable:
            prompt = (
                "Пользователь прислал файл (возможно изображение). "
                "Напиши одно короткое предложение для ответа в чат: например что файл сохранён, "
                "по изображениям описать не могу, или что можно спросить о документе. Без кавычек и лишнего."
            )
        else:
            excerpt = extracted_text[:4000].strip()
            if len(extracted_text) > 4000:
                excerpt += "\n\n[...]"
            prompt = (
                "Кратко опиши содержимое документа для пользователя (2–4 предложения). "
                "Только суть, без вводных фраз вроде «В документе…». Текст документа:\n\n" + excerpt
            )
        try:
            gateway = await self._gateway_factory()
            out = await gateway.generate(
                prompt,
                system="Ты помогаешь кратко резюмировать содержимое файла для пользователя. Ответь только текстом для чата.",
            )
            summary = (out or "").strip() if isinstance(out, str) else ""
            if summary and len(summary) > 2000:
                summary = summary[:1997] + "..."
            return summary or "Файл проиндексирован. Можешь спросить что в нём."
        except Exception as e:
            logger.warning("File summary generation: %s", e)
            return "Файл проиндексирован. Можешь спросить что в нём."

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
