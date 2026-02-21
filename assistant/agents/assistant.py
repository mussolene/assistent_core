"""AssistantAgent: loads memory, calls Model Gateway, can request tool use."""

from __future__ import annotations

import json
import logging
import re
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
    if "500" in err or "502" in err or "503" in err or "internal server error" in err or "bad gateway" in err:
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
    return f"Ошибка модели: {one_line}" if one_line else "Ошибка модели. Проверьте настройки и логи."


SYSTEM_PROMPT = """You are a helpful personal assistant. You can use tools when needed.
When you need to read a file, run a command, or search memory, respond with a JSON block like:
{"tool_calls": [{"name": "filesystem", "params": {"action": "read", "path": "/path/to/file"}}]}
Skills:
- filesystem: read, list, write (path, action).
- shell: whitelisted commands (ls, cat, git, pytest, python, etc.).
- git: clone (url, dir?), read (path, rev?, repo_dir?), list_repos/list_cloned (repos in workspace with remote_url), status/diff/log, commit, push, create_mr. For clone/push network enabled; for create_mr set GITHUB_TOKEN or GITLAB_TOKEN.
- vector_rag: search, add (action, text?).
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
        if (model_gateway is None and gateway_factory is None) or (model_gateway is not None and gateway_factory is not None):
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
            user_content += "\n\nTool results:\n" + "\n".join(
                str(r) for r in context.tool_results
            )
        prompt_parts = []
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
        for m in re.finditer(r"\{[^{}]*\"tool_calls\"[^{}]*\}", text):
            try:
                obj = json.loads(m.group())
                calls = obj.get("tool_calls", [])
                out.extend(calls)
            except json.JSONDecodeError:
                continue
        return out
