"""AssistantAgent: loads memory, calls Model Gateway, can request tool use."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from assistant.agents.base import AgentResult, BaseAgent, TaskContext
from assistant.memory.manager import MemoryManager
from assistant.models.gateway import ModelGateway

logger = logging.getLogger(__name__)


def _format_model_error_for_user(exc: Exception) -> str:
    """Превращает исключение от модели в короткое сообщение для пользователя (без HTML и сырых тел ответов)."""
    err = str(exc).strip().lower()
    raw = str(exc).strip()
    # Сырой HTML или длинный ответ — не показывать пользователю
    if "<html" in err or "<!doctype" in err or (raw.startswith("<") and ">" in raw):
        if "403" in err or "forbidden" in err:
            return (
                "Сервер модели вернул 403 (доступ запрещён). "
                "Проверьте API ключ, URL и права доступа к сервису модели."
            )
        if "404" in err or "not found" in err:
            return "Сервер модели не найден (404). Проверьте OPENAI_BASE_URL в настройках."
        if "500" in err or "502" in err or "503" in err:
            return "Сервер модели временно недоступен (ошибка 5xx). Попробуйте позже."
        return "Сервер модели вернул ошибку. Проверьте настройки и доступность сервиса."
    # Обычный текст ошибки
    if "403" in err or "forbidden" in err:
        return (
            "Сервер модели вернул 403 (доступ запрещён). "
            "Проверьте API ключ, URL и права доступа к сервису модели."
        )
    if "404" in err or "not found" in err:
        return "Сервер модели не найден (404). Проверьте OPENAI_BASE_URL в настройках."
    if (
        "500" in err
        or "502" in err
        or "503" in err
        or "internal server error" in err
        or "bad gateway" in err
    ):
        return "Сервер модели временно недоступен (ошибка 5xx). Попробуйте позже."
    if "connection" in err or "connect" in err or "refused" in err:
        return (
            "Модель недоступна. Убедитесь, что Ollama запущена на хосте и в .env задан "
            "OPENAI_BASE_URL (например http://host.docker.internal:11434/v1 для Docker)."
        )
    if "400" in err or "bad request" in err:
        return (
            "Ошибка 400 от сервера модели. Проверьте в настройках: имя модели совпадает с загруженной "
            "(например в LM Studio), URL заканчивается на /v1 для OpenAI-совместимого API или включён «LM Studio native»."
        )
    # Короткая ошибка — можно показать первую строку (без переносов), но не длиннее ~120 символов
    one_line = raw.replace("\n", " ").replace("\r", " ").strip()
    if len(one_line) > 120:
        one_line = one_line[:117] + "..."
    return (
        f"Ошибка модели: {one_line}" if one_line else "Ошибка модели. Проверьте настройки и логи."
    )


SYSTEM_PROMPT = """You are a helpful personal assistant. You can use tools when needed.
When you need to read a file, run a command, or search memory, respond with a JSON block like:
{"tool_calls": [{"name": "filesystem", "params": {"action": "read", "path": "/path/to/file"}}]}
Skills:
- filesystem: read, list, write (path, action).
- shell: whitelisted commands (ls, cat, git, pytest, python, etc.).
- git: clone, read, list_repos/list_cloned, search_repos (platform=github, query), status/diff/log, commit, push, create_mr. GITHUB_TOKEN for search_repos and create_mr.
- vector_rag: search, add (action, text?). Поиск по проиндексированному тексту (в т.ч. из присланных файлов).
- file_ref: list (список сохранённых файлов пользователя после индексации вложений), send (file_ref_id — отправить файл в чат). Для «отправь файл X» или «скачай тот файл» вызови file_ref list, затем send с нужным file_ref_id.
Файлы и документы: если пользователь прислал файл или спрашивает «что тут написано», «что в документе», «о чём этот файл» — речь о содержимом присланного файла. Обязательно вызови vector_rag search с запросом (например по теме вопроса или «содержимое документа»), получи результаты и ответь по ним. Не отказывай и не путай с «задачами» (tasks — список дел): ответ «не могу показать содержимое задач» на вопрос про файл/документ неверен — пользователь спрашивает про текст из вложения, а не про список задач.
- checklist: create (title, tasks — массив {id?, text}; до 30 пунктов). Отправляет чеклист в чат (если настроен business_connection_id — нативный Telegram-чеклист, иначе текстовый список). Для «создай чеклист», «список дел на день» вызови checklist с title и tasks.
- tasks: имена действий с подчёркиванием: list_tasks, create_task, delete_task, update_task, get_task, search_tasks, add_document, add_link, set_reminder, format_for_telegram. create_task (title, description?, start_date?, end_date?, workload?, time_spent?), update_task (task_id, title?, start_date?, end_date?, workload?, time_spent?, time_spent_minutes?, cascade?=true), list_tasks (возвращает tasks и formatted — готовый текст списка с датами и загрузкой), get_task, search_tasks (query), add_document, add_link, set_reminder. user_id подставляется автоматически.
Список задач: на запрос «список задач», «мои задачи» вызови list_tasks(only_actual=true). В списке показываются заголовок и дата создания каждой задачи; ответ с formatted и inline_keyboard отправляется автоматически (без кнопки «✓ Выполнена» в списке). Никогда не выводи пользователю JSON или tool_calls.
Порядковые номера: «первая/вторая/третья задача», «задачу номер 2», «поставь вторую как выполненную», «покажи что во второй задаче» — имеется в виду номер в списке задач (1-based). Всегда: сначала list_tasks (only_actual=false если нужны все), взять задачу по индексу: первая = tasks[0], вторая = tasks[1], третья = tasks[2] (индекс = номер минус 1). Дальше: для «отметить выполненной» — update_task(task_id=tasks[N].id, status="done"); для «покажи детали второй» — get_task(task_id=tasks[1].id) и вывести пользователю поле formatted_details из ответа. Не отказывай в обновлении статуса «по номеру» — достаточно вызвать list_tasks, взять id по номеру и update_task.
Работа с задачами на естественном языке: создание — create_task. Удалить/править/добавить к «задаче о X»: search_tasks(query), затем при одном совпадении — действие, при нескольких — format_for_telegram с кнопками. Затраченное время и оценка загрузки: update_task с time_spent или workload. При переносе дат — cascade.
Даты: передавай start_date и end_date в create_task/update_task только если пользователь явно назвал дату или срок («на понедельник», «до 25 февраля», «к пятнице», «завтра»). Если пользователь только описал задачу без даты — не передавай start_date и end_date (не придумывай даты). При указании даты без года используй текущий год.
Напоминания: set_reminder(task_id, reminder_at) — reminder_at всегда в ISO datetime в UTC (например 2025-02-22T12:35:00+00:00 или 2025-02-22T12:35:00Z). «Напомни завтра в 10:00» — явное время в ISO (UTC). «Напомни про неё через 5 минут»: определи задачу (из контекста или list_tasks), вычисли reminder_at = сейчас + N минут в UTC, вызови set_reminder один раз. После успешного результата set_reminder в Tool results отвечай пользователю одной короткой фразой («Напоминание установлено на …») и больше не вызывай инструменты (никаких tool_calls после установки напоминания).
Помогай с решением задач: предлагай шаги, напоминай о дедлайнах.
Keep answers concise. Do not make up file contents or command output."""


class AssistantAgent(BaseAgent):
    """Calls model with context; returns text or tool_calls. Gateway can be fixed or from factory (config applied on each request)."""

    def __init__(
        self,
        model_gateway: ModelGateway | None = None,
        memory: MemoryManager | None = None,
        gateway_factory: Callable[[], Awaitable[ModelGateway]] | None = None,
    ) -> None:
        self._model = model_gateway
        self._memory = memory
        self._gateway_factory = gateway_factory
        if (model_gateway is None and gateway_factory is None) or (
            model_gateway is not None and gateway_factory is not None
        ):
            raise ValueError("Provide exactly one of model_gateway or gateway_factory")

    async def _get_gateway(self) -> ModelGateway:
        if self._gateway_factory:
            return await self._gateway_factory()
        assert self._model is not None
        return self._model

    async def handle(self, context: TaskContext) -> AgentResult:
        messages = await self._memory.get_context_for_user(
            context.user_id, context.task_id, include_vector=True
        )
        if context.text:
            await self._memory.append_message(context.user_id, "user", context.text)
        user_content = context.text
        if context.tool_results:
            user_content += "\n\nTool results:\n" + "\n".join(str(r) for r in context.tool_results)
        prompt_parts = []
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prompt_parts.append(
            f"Current date: {today_iso}. Use this when interpreting relative dates (e.g. 'завтра', 'пятница') or when the user gives a date without year."
        )
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                continue
            prompt_parts.append(f"{role.capitalize()}: {content}")
        prompt_parts.append(f"User: {user_content}")
        full_prompt = "\n\n".join(prompt_parts)
        stream_cb = context.metadata.get("stream_callback")
        model = await self._get_gateway()
        try:
            if stream_cb:
                stream = model.generate(
                    full_prompt,
                    stream=True,
                    reasoning=context.reasoning_requested,
                    system=SYSTEM_PROMPT,
                )
                if hasattr(stream, "__aiter__"):
                    full = ""
                    async for token in stream:
                        full += token
                        await stream_cb(token, done=False)
                    await stream_cb("", done=True)
                    text = full
                else:
                    text = await model.generate(
                        full_prompt,
                        stream=False,
                        reasoning=context.reasoning_requested,
                        system=SYSTEM_PROMPT,
                    )
            else:
                text = await model.generate(
                    full_prompt,
                    stream=False,
                    reasoning=context.reasoning_requested,
                    system=SYSTEM_PROMPT,
                )
        except Exception as e:
            logger.exception("model generate failed: %s", e)
            if stream_cb:
                await stream_cb("", done=True)
            user_msg = _format_model_error_for_user(e)
            return AgentResult(success=True, output_text=user_msg, error=str(e))
        if context.text and not context.tool_results:
            await self._memory.append_message(context.user_id, "assistant", text)
        tool_calls = self._parse_tool_calls(text)
        if tool_calls:
            return AgentResult(
                success=True,
                output_text=text,
                tool_calls=tool_calls,
                next_agent="tool",
            )
        return AgentResult(success=True, output_text=text)

    def _parse_tool_calls(self, text: str) -> list[dict[str, Any]]:
        out = []
        for key in ("tool_calls", "toolcalls"):
            idx = text.lower().find(f'"{key}"')
            if idx < 0:
                continue
            start = text.rfind("{", 0, idx)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : i + 1])
                            calls = obj.get("tool_calls") or obj.get("toolcalls") or []
                            if isinstance(calls, list):
                                out.extend(calls)
                        except json.JSONDecodeError:
                            pass
                        break
        return out
