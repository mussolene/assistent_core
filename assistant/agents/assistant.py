"""AssistantAgent: loads memory, calls Model Gateway, can request tool use."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from assistant.agents.base import AgentResult, BaseAgent, TaskContext
from assistant.memory.manager import MemoryManager
from assistant.models.gateway import ModelGateway

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful personal assistant. You can use tools when needed.
When you need to read a file, run a command, or search memory, respond with a JSON block like:
{"tool_calls": [{"name": "filesystem", "params": {"action": "read", "path": "/path/to/file"}}]}
Use the skills: filesystem (read/list/write), shell (whitelisted commands), git (status, diff, log), vector_rag (search/add).
Keep answers concise. Do not make up file contents or command output."""


class AssistantAgent(BaseAgent):
    """Calls model with context; returns text or tool_calls."""

    def __init__(
        self,
        model_gateway: ModelGateway,
        memory: MemoryManager,
    ) -> None:
        self._model = model_gateway
        self._memory = memory

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
        try:
            if context.metadata.get("stream"):
                stream = self._model.generate(
                    full_prompt,
                    stream=True,
                    reasoning=context.reasoning_requested,
                    system=SYSTEM_PROMPT,
                )
                if hasattr(stream, "__aiter__"):
                    full = ""
                    async for token in stream:
                        full += token
                    return AgentResult(success=True, output_text=full)
            text = await self._model.generate(
                full_prompt,
                stream=False,
                reasoning=context.reasoning_requested,
                system=SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.exception("model generate failed: %s", e)
            err_msg = str(e).lower()
            if "connection" in err_msg or "connect" in err_msg or "refused" in err_msg:
                user_msg = (
                    "Модель недоступна. Убедитесь, что Ollama запущена на хосте и в .env задан "
                    "OPENAI_BASE_URL (например http://host.docker.internal:11434/v1 для Docker)."
                )
            else:
                user_msg = f"Ошибка модели: {e}"
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
