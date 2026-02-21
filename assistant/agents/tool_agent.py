"""ToolAgent: runs skill calls from context and returns results to Orchestrator."""

from __future__ import annotations

import logging

from assistant.agents.base import AgentResult, BaseAgent, TaskContext
from assistant.memory.manager import MemoryManager
from assistant.skills.registry import SkillRegistry
from assistant.skills.runner import SandboxRunner

logger = logging.getLogger(__name__)


class ToolAgent(BaseAgent):
    """Interprets tool_calls from context; runs skills via registry in sandbox; returns results."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        sandbox_runner: SandboxRunner,
        memory: MemoryManager,
    ) -> None:
        self._registry = skill_registry
        self._runner = sandbox_runner
        self._memory = memory

    async def handle(self, context: TaskContext) -> AgentResult:
        tool_calls = context.metadata.get("pending_tool_calls") or []
        if not tool_calls:
            return AgentResult(success=False, error="no tool_calls in context")
        results = []
        for call in tool_calls:
            name = call.get("name") or call.get("skill")
            params = dict(call.get("params") or call.get("arguments") or {})
            if not name:
                results.append({"error": "missing skill name", "ok": False})
                continue
            if name == "tasks":
                if "user_id" not in params and context.user_id:
                    params["user_id"] = context.user_id
                action = params.get("action")
                if action is not None:
                    from assistant.skills.tasks import _normalize_action
                    params["action"] = _normalize_action(str(action))
                from assistant.skills.tasks import _normalize_task_params
                params = _normalize_task_params(params)
            result = await self._registry.run(name, params, self._runner)
            results.append(result)
            await self._memory.append_tool_result(context.task_id, name, result)
        return AgentResult(
            success=True,
            output_text="",
            tool_calls=None,
            next_agent="assistant",
            metadata={"tool_results": results},
        )
