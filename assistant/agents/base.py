"""Base agent interface. All agents are stateless; state in central store."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TaskContext:
    """Context for one task. Read/write via central store."""

    task_id: str
    user_id: str
    chat_id: str
    channel: str
    message_id: str
    text: str
    reasoning_requested: bool
    state: str
    iteration: int
    tool_results: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass
class AgentResult:
    """Result from handle(). Orchestrator uses this for state transition."""

    success: bool
    output_text: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    next_agent: str | None = None
    error: str | None = None
    stream_id: str | None = None
    metadata: dict[str, Any] | None = None


class BaseAgent(ABC):
    """Stateless agent. handle(task_context) -> AgentResult."""

    @abstractmethod
    async def handle(self, context: TaskContext) -> AgentResult:
        pass
