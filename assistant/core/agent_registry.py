"""Agent Registry: map agent type to instance. Orchestrator looks up and calls handle()."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from assistant.agents.base import BaseAgent, TaskContext, AgentResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Register agents by type (assistant, tool, etc.)."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent_type: str, agent: BaseAgent) -> None:
        self._agents[agent_type] = agent
        logger.debug("registered agent: %s", agent_type)

    def get(self, agent_type: str) -> BaseAgent | None:
        return self._agents.get(agent_type)

    async def handle(self, agent_type: str, context: TaskContext) -> AgentResult:
        agent = self.get(agent_type)
        if not agent:
            return AgentResult(success=False, error=f"unknown agent: {agent_type}")
        return await agent.handle(context)
